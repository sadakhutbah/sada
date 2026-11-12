"""Content of a khutba (corrected transcription, verses, translations) and lifecycle of the clips:
corrections -> regeneration (clips.build_ass + render_clip pipeline) -> validation / rejection.

Pipeline files are never modified: corrections live in the database and are applied in memory
(display and regeneration). A regenerated clip is a NEW file (version n+1) that goes back to "to validate".
"""

from __future__ import annotations

import copy
import json
import math
import shutil
import threading
import traceback
from pathlib import Path

from ..i18n import _
from ..pipeline import clips as clipmod
from ..pipeline import translate, verses
from ..pipeline.output import SessionOutput
from . import db

# Clip statuses, in the order of the filter tabs. Protocol keys only: the front end translates them.
CLIP_STATUSES = ("a_valider", "valide", "publie", "rejete")
REQUEUE = "status = 'a_valider', decided_by = NULL, decided_at = NULL"


def corrections(conn, khutba_id: int) -> dict[tuple[int, str], dict]:
    return {
        (c["seg_index"], c["field"]): c
        for c in db.all_(conn, "SELECT * FROM corrections WHERE khutba_id = ?", khutba_id)
    }


def added(conn, khutba_id: int) -> list[dict]:
    return db.all_(conn, "SELECT * FROM added_subtitles WHERE khutba_id = ? ORDER BY start", khutba_id)


def load(conn, k: dict) -> dict:
    """Transcription + verses + translations of the khutba, corrections applied."""
    o = SessionOutput(k["output_dir"])
    tr = o.read(o.transcript, {"segments": []})
    segs = copy.deepcopy(tr.get("segments", []))
    trans = o.read(o.translations, translate.empty())
    ms = o.read(o.verses, [])
    corr = corrections(conn, k["id"])
    for i, s in enumerate(segs):
        s["_i"] = i  # original index: corrections are keyed on it
    for (i, field), c in corr.items():
        if i >= len(segs):
            continue
        if field == "text":
            segs[i]["text"] = c["value"]
            segs[i].pop("words", None)  # word-level timestamps lost: build_ass interpolates proportionally
        elif field == "fr":
            trans = translate.with_correction(trans, segs[i], c["value"])
    # Hand-added subtitles join the timeline as French speech segments (no word timestamps).
    for a in added(conn, k["id"]):
        segs.append({"start": a["start"], "end": a["end"], "text": a["text"], "_added": a["id"]})
    segs.sort(key=lambda s: (s["start"], s["end"]))
    return {
        "segments": segs,
        "translations": trans,
        "verses": ms,
        "corrections": corr,
        "duration": tr.get("duration"),
        "model": tr.get("model"),
        "language": tr.get("language"),
    }


def transcript_rows(data: dict, t0: float = 0.0, t1: float | None = None) -> list[dict]:
    """Rows to display: original text, French subtitle shown in the clips, verse if any, correctable field."""
    vms = verses.shown(data["verses"], ambiguous=True)
    rows = []
    for s in data["segments"]:
        if t1 is not None and not (s["end"] > t0 and s["start"] < t1):
            continue
        i = s.get("_i", -1)
        if s.get("_added"):
            rows.append(
                {
                    "i": -s["_added"],
                    "added": s["_added"],
                    "start": s["start"],
                    "end": s["end"],
                    "rel": max(0.0, s["start"] - t0),
                    "text": s["text"],
                    "arabic": False,
                    "coran": False,
                    "verse": None,
                    "fr": s["text"],
                    "field": None,
                    "corrected": False,
                }
            )
            continue
        sub = clipmod.subtitle(s, vms, data["translations"])
        m = sub.get("verse")
        rows.append(
            {
                "i": i,
                "start": s["start"],
                "end": s["end"],
                "rel": max(0.0, s["start"] - t0),
                "text": s["text"],
                "arabic": sub["kind"] != "speech",
                "coran": sub["kind"] == "recitation",
                "verse": {
                    "ref": m["ref"],
                    "surah": m.get("surah_name", ""),
                    "ambiguous": m.get("ambiguous", False),
                    "fr": sub["fr"],
                }
                if m
                else None,
                "fr": sub["fr"] if sub["kind"] in ("speech", "translation") else None,
                "field": {"speech": "text", "translation": "fr"}.get(sub["kind"]),
                "corrected": any((i, f) in data["corrections"] for f in ("text", "fr")),
            }
        )
    return rows


def save_correction(conn, k: dict, seg_index: int, field: str, value: str, user: dict, ip=None) -> int:
    """Saves a correction; the clips covering this segment go back to "to validate" and must be regenerated.
    Returns the number of affected clips."""
    value = " ".join(value.split())
    data = load(conn, k)
    seg = next((s for s in data["segments"] if s.get("_i") == seg_index), None)
    if seg is None:
        raise ValueError(_("unknown segment"))
    row = next((r for r in transcript_rows(data) if r["i"] == seg_index), None)
    if row is None or row["field"] != field:
        raise ValueError(
            _(
                "this passage cannot be corrected (verse: the Hamidullah translation is fixed; "
                "unmatched recitation: no subtitle)"
            )
        )
    if not value:
        raise ValueError(_("empty text"))
    current = row["fr"] if field == "fr" else row["text"]
    if value == current:
        return 0
    old = db.one(
        conn, "SELECT * FROM corrections WHERE khutba_id = ? AND seg_index = ? AND field = ?", k["id"], seg_index, field
    )
    original = old["original"] if old else (current or "")
    conn.execute(
        "INSERT INTO corrections (mosque_id, khutba_id, seg_index, field, original, value, user_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (khutba_id, seg_index, field) DO UPDATE SET "
        "value = excluded.value, user_id = excluded.user_id, created_at = excluded.created_at",
        (k["mosque_id"], k["id"], seg_index, field, original, value, user["id"], db.now_iso()),
    )
    hit = _requeue(conn, k, seg["start"], seg["end"])
    db.audit(
        conn,
        k["mosque_id"],
        user["id"],
        "correction." + ("sous-titre" if field == "text" else "traduction"),
        "khutba",
        k["id"],
        ip=ip,
        segment=seg_index,
        avant=current,
        apres=value,
        clips=[c["id"] for c in hit],
    )
    return len(hit)


def _requeue(conn, k: dict, start: float, end: float) -> list[dict]:
    """Clips covering [start, end] must be regenerated (and validated again)."""
    hit = db.all_(
        conn,
        "SELECT id, status FROM clips WHERE khutba_id = ? AND start < ? AND \"end\" > ? AND status != 'rejete'",
        k["id"],
        end,
        start,
    )
    for c in hit:
        if c["status"] == "publie":
            # already published: we do not rewrite history; the corrected version (v+1) goes through validation again
            conn.execute("UPDATE clips SET needs_regen = 1, updated_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
        else:
            conn.execute(
                f"UPDATE clips SET needs_regen = 1, {REQUEUE}, updated_at = ? WHERE id = ?", (db.now_iso(), c["id"])
            )
    return hit


MIN_ADDED, MAX_ADDED = 0.3, 30.0


def add_subtitle(conn, k: dict, start: float, end: float, text: str, user: dict, ip=None) -> int:
    """Adds a French subtitle at a precise instant (session time), for a passage the transcription missed.
    Refused over a passage that already shows something (subtitle, translation, verse card): correct it instead.
    Returns the number of clips to regenerate."""
    text = " ".join((text or "").split())
    if not text:
        raise ValueError(_("empty text"))
    start, end = round(float(start), 2), round(float(end), 2)
    if start < 0 or not (MIN_ADDED <= end - start <= MAX_ADDED):
        raise ValueError(_("invalid duration: between {low} and {high} s", low=f"{MIN_ADDED:g}", high=f"{MAX_ADDED:g}"))
    data = load(conn, k)
    dur = data.get("duration")
    if dur and end > dur + 0.5:
        raise ValueError(_("beyond the end of the recording"))
    for r in transcript_rows(data, start, end):
        if r.get("added") or r["fr"] or r["verse"]:
            raise ValueError(
                _(
                    "this moment already has a subtitle ({start}–{end} s): correct it instead",
                    start=f"{r['start']:.1f}",
                    end=f"{r['end']:.1f}",
                )
            )
    cur = conn.execute(
        'INSERT INTO added_subtitles (mosque_id, khutba_id, start, "end", text, user_id, created_at) '
        "VALUES (?,?,?,?,?,?,?)",
        (k["mosque_id"], k["id"], start, end, text, user["id"], db.now_iso()),
    )
    hit = _requeue(conn, k, start, end)
    db.audit(
        conn,
        k["mosque_id"],
        user["id"],
        "correction.ajout",
        "khutba",
        k["id"],
        ip=ip,
        ajout=cur.lastrowid,
        debut=start,
        fin=end,
        apres=text,
        clips=[c["id"] for c in hit],
    )
    return len(hit)


def remove_subtitle(conn, k: dict, sid: int, user: dict, ip=None) -> int:
    a = db.one(conn, "SELECT * FROM added_subtitles WHERE id = ? AND khutba_id = ?", sid, k["id"])
    if not a:
        raise ValueError(_("unknown added subtitle"))
    conn.execute("DELETE FROM added_subtitles WHERE id = ?", (sid,))
    hit = _requeue(conn, k, a["start"], a["end"])
    db.audit(
        conn,
        k["mosque_id"],
        user["id"],
        "correction.retrait",
        "khutba",
        k["id"],
        ip=ip,
        ajout=sid,
        avant=a["text"],
        clips=[c["id"] for c in hit],
    )
    return len(hit)


# ------------------------------------------------------------------ regeneration


def can_regenerate(clip: dict) -> tuple[bool, str]:
    if not shutil.which("ffmpeg"):
        return False, _("ffmpeg not found on this machine")
    if not clip.get("video_source") or not Path(clip["video_source"]).exists():
        return False, _("clip source video not found")
    if clip.get("source_offset") is None:
        return False, _("unknown source offset (old manifest)")
    return True, ""


def recover_interrupted(conn) -> None:
    """Regenerations in progress when the server stopped: they will never finish."""
    conn.execute(
        "UPDATE clips SET regen_state = 'erreur', regen_error = ?, updated_at = ? WHERE regen_state = 'en_cours'",
        (_("interrupted (server stopped)"), db.now_iso()),
    )


def _render(db_path: str, clip_id: int, log=print) -> None:
    conn = db.connect(db_path)
    try:
        c = db.one(conn, "SELECT * FROM clips WHERE id = ?", clip_id)
        k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", c["khutba_id"])
        m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", c["mosque_id"])
        data = load(conn, k)
        ver = c["version"] + 1
        # Même identité que le clip d'origine : logo et couleurs de la mosquée quand ils existent.
        brand = {k: m[f"brand_{k}"] for k in ("green", "gold") if (m[f"brand_{k}"] or "").strip()}
        logo = None
        if (m.get("logo_path") or "").strip():
            from . import branding

            p = branding.path_for(m)
            if p.exists() and p.suffix.lower() in clipmod.LOGO_EXT:
                logo = str(p)
        out = clipmod.make_clip(
            data["segments"],
            data["verses"],
            data["translations"],
            {"start": c["start"], "end": c["end"], "kind": c["kind"]},
            c["idx"],
            ver,
            c["video_source"],
            c["source_offset"],
            Path(c["file_path"]).parent,
            {"title": m["clip_title"], "subtitle": c.get("title") or ""},
            json.loads(c["crop"]) if c["crop"] else None,
            log,
            logo=logo,
            brand=brand or None,
            # bornes choisies par un humain : rendues telles quelles
            trim_tail=not (c.get("origin") == "manuel" or c.get("bounds_by")),
        )
        conn.execute(
            "UPDATE clips SET file_path = ?, version = ?, needs_regen = 0, regen_state = '', regen_error = '', "
            f"{REQUEUE}, updated_at = ? WHERE id = ?",
            (str(out), ver, db.now_iso(), clip_id),
        )
        db.audit(conn, c["mosque_id"], None, "clip.regenere", "clip", clip_id, version=ver, fichier=out.name)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        conn.execute(
            "UPDATE clips SET regen_state = 'erreur', regen_error = ?, updated_at = ? WHERE id = ?",
            (str(e)[:500], db.now_iso(), clip_id),
        )
    finally:
        conn.close()


def start_regeneration(conn, db_path: str, clip: dict, user: dict, ip=None, background: bool = True) -> None:
    if not (clip["needs_regen"] or clip["regen_state"] == "erreur"):
        raise ValueError(_("no correction to apply: the clip is already approved"))
    ok, why = can_regenerate(clip)
    if not ok:
        raise ValueError(_("regeneration impossible: {why}", why=why))
    if clip["regen_state"] == "en_cours":
        raise ValueError(_("regeneration already in progress"))
    conn.execute(
        "UPDATE clips SET regen_state = 'en_cours', regen_error = '', updated_at = ? WHERE id = ?",
        (db.now_iso(), clip["id"]),
    )
    db.audit(
        conn, clip["mosque_id"], user["id"], "clip.regeneration", "clip", clip["id"], ip=ip, version=clip["version"] + 1
    )
    if background:
        threading.Thread(target=_render, args=(db_path, clip["id"]), daemon=True, name=f"regen-{clip['id']}").start()
    else:
        _render(db_path, clip["id"])


# ------------------------------------------------------------------ decision


def can_validate(clip: dict) -> tuple[bool, str]:
    if clip["needs_regen"] or clip["regen_state"] == "en_cours":
        return False, _("corrections are not in the video yet: regenerate the clip before approving it")
    if not Path(clip["file_path"]).exists():
        return False, _("clip file not found")
    return True, ""


def decide(conn, clip: dict, decision: str, user: dict, reason: str = "", ip=None) -> None:
    if decision not in ("valide", "rejete"):
        raise ValueError(_("unknown decision"))
    if clip["status"] == "publie":
        raise ValueError(_("clip already published: decision is final"))
    if decision == "valide":
        ok, why = can_validate(clip)
        if not ok:
            raise ValueError(why)
    conn.execute(
        "UPDATE clips SET status = ?, decided_by = ?, decided_at = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
        (
            decision,
            user["id"],
            db.now_iso(),
            reason.strip()[:500] if decision == "rejete" else "",
            db.now_iso(),
            clip["id"],
        ),
    )
    db.audit(
        conn,
        clip["mosque_id"],
        user["id"],
        "clip." + ("valide" if decision == "valide" else "rejete"),
        "clip",
        clip["id"],
        ip=ip,
        version=clip["version"],
        motif=reason.strip()[:500] or None,
    )


def reopen(conn, clip: dict, user: dict, ip=None) -> None:
    if clip["status"] not in ("valide", "rejete"):
        raise ValueError(_("only an approved or rejected clip can be put back in the queue"))
    conn.execute(
        f"UPDATE clips SET {REQUEUE}, reject_reason = '', updated_at = ? WHERE id = ?", (db.now_iso(), clip["id"])
    )
    db.audit(conn, clip["mosque_id"], user["id"], "clip.remis_en_file", "clip", clip["id"], ip=ip)


# ------------------------------------------------------------------ clips chosen by a human
# The model proposes excerpts but can miss: the person running the tool creates a clip by hand
# (start/end chosen on the khutba video) or moves the bounds of a proposed one. Same circuit as
# the model's clips: render with subtitles (make_clip, via the regeneration thread), "to validate",
# audit, publication.

CLIP_KINDS = ("extrait", "verset", "rappel", "citation", "passage", "conclusion")
MIN_CLIP, MAX_CLIP = 5.0, 180.0
MAX_TITLE = 120


def clip_bounds(k: dict, start, end, duration: float | None = None) -> tuple[float, float]:
    """Bounds in session time, checked: inside the recording, between MIN_CLIP and MAX_CLIP seconds."""
    try:
        start, end = float(str(start).replace(",", ".")), float(str(end).replace(",", "."))
    except ValueError:
        raise ValueError(_("unreadable bounds")) from None
    if not (math.isfinite(start) and math.isfinite(end)):
        raise ValueError(_("unreadable bounds"))
    start, end = round(start, 2), round(end, 2)
    if start < 0:
        raise ValueError(_("the start is before the recording's start"))
    if end <= start:
        raise ValueError(_("the end must come after the start"))
    if not (MIN_CLIP <= end - start <= MAX_CLIP):
        raise ValueError(
            _("invalid duration: a clip lasts between {low} and {high} s", low=f"{MIN_CLIP:g}", high=f"{MAX_CLIP:g}")
        )
    dur = k.get("duration_s") or duration
    if dur and end > dur + 0.5:
        raise ValueError(_("the end is past the end of the recording"))
    return start, end


def clip_source(conn, k: dict) -> dict:
    """Where a new clip of this khutba is cut from: the source of its other clips (same recording,
    same offset, same crop), otherwise the live recording, otherwise the extracted khutba video."""
    crop = None
    candidates = []
    for c in db.all_(
        conn,
        "SELECT video_source, source_offset, crop FROM clips WHERE khutba_id = ? "
        "AND video_source IS NOT NULL AND source_offset IS NOT NULL ORDER BY id",
        k["id"],
    ):
        candidates.append((c["video_source"], c["source_offset"]))
        crop = crop or c["crop"]
    if k.get("recording") and k.get("start_pos") is not None:
        candidates.append((k["recording"], k["start_pos"]))
    if k.get("video_path"):
        candidates.append((k["video_path"], 0.0))  # the extracted video starts at the session start
    found = next((c for c in candidates if Path(c[0]).exists()), candidates[0] if candidates else (None, None))
    return {"video_source": found[0], "source_offset": found[1], "crop": crop}


def can_create_clip(conn, k: dict) -> tuple[bool, str]:
    return can_regenerate(clip_source(conn, k))


def _clean_title(title) -> str:
    return " ".join(str(title or "").split())[:MAX_TITLE]


def create_clip(
    conn, db_path: str, k: dict, start, end, kind: str, title: str, user: dict, ip=None, background: bool = True
) -> int:
    """A clip chosen by hand: stored "to validate" (version 0, nothing rendered yet), then rendered with
    the subtitles by the regeneration mechanism (version 1). Returns the clip id."""
    kind = (kind or "extrait").strip()
    if kind not in CLIP_KINDS:
        raise ValueError(_("unknown clip type"))
    start, end = clip_bounds(k, start, end, load(conn, k).get("duration"))
    src = clip_source(conn, k)
    ok, why = can_regenerate(src)
    if not ok:
        raise ValueError(_("clip cannot be produced: {why}", why=why))
    idx = (db.one(conn, "SELECT MAX(idx) n FROM clips WHERE khutba_id = ?", k["id"])["n"] or 0) + 1
    outdir = SessionOutput(k["output_dir"]).clips
    outdir.mkdir(parents=True, exist_ok=True)
    t = db.now_iso()
    cur = conn.execute(
        'INSERT INTO clips (mosque_id, khutba_id, idx, kind, why, start, "end", file_path, video_source, source_offset, '
        "crop, version, needs_regen, origin, title, created_by, bounds_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,1,'manuel',?,?,?,?,?)",
        (
            k["mosque_id"],
            k["id"],
            idx,
            kind,
            "",
            start,
            end,
            str(outdir / f"clip{idx}_{kind}_A-VALIDER.mp4"),
            src["video_source"],
            src["source_offset"],
            src["crop"],
            _clean_title(title),
            user["id"],
            user["id"],
            t,
            t,
        ),
    )
    cid = cur.lastrowid
    db.audit(
        conn,
        k["mosque_id"],
        user["id"],
        "clip.cree",
        "clip",
        cid,
        ip=ip,
        khutba=k["id"],
        type=kind,
        debut=start,
        fin=end,
        titre=_clean_title(title) or None,
    )
    start_regeneration(
        conn, db_path, db.one(conn, "SELECT * FROM clips WHERE id = ?", cid), user, ip, background=background
    )
    return cid


def adjust_clip(
    conn, db_path: str, clip: dict, k: dict, start, end, title, user: dict, ip=None, background: bool = True
) -> None:
    """New bounds (and title) for a clip, proposed by the model or not: it goes back to "to validate"
    and is rendered again (version n+1). A published clip is frozen: create a new clip instead."""
    if clip["status"] == "publie":
        raise ValueError(_("clip already published: its bounds are frozen, create a new clip instead"))
    if clip["regen_state"] == "en_cours":
        raise ValueError(_("regeneration already in progress"))
    start, end = clip_bounds(k, start, end, load(conn, k).get("duration"))
    title = clip["title"] if title is None else _clean_title(title)
    if (start, end, title) == (round(clip["start"], 2), round(clip["end"], 2), clip["title"]):
        raise ValueError(_("nothing changed"))
    ok, why = can_regenerate(clip)
    if not ok:
        raise ValueError(_("regeneration impossible: {why}", why=why))
    moved = (start, end) != (round(clip["start"], 2), round(clip["end"], 2))
    conn.execute(
        f'UPDATE clips SET start = ?, "end" = ?, title = ?, bounds_by = ?, needs_regen = 1, {REQUEUE}, '
        "reject_reason = '', updated_at = ? WHERE id = ?",
        (start, end, title, user["id"] if moved else clip["bounds_by"], db.now_iso(), clip["id"]),
    )
    db.audit(
        conn,
        clip["mosque_id"],
        user["id"],
        "clip.bornes",
        "clip",
        clip["id"],
        ip=ip,
        avant=[clip["start"], clip["end"]],
        apres=[start, end],
        titre=title if title != clip["title"] else None,
    )
    start_regeneration(
        conn, db_path, db.one(conn, "SELECT * FROM clips WHERE id = ?", clip["id"]), user, ip, background=background
    )
