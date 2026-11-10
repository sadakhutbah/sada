"""Content of a khutba (corrected transcription, verses, translations) and lifecycle of the clips:
corrections -> regeneration (clips.build_ass + render_clip pipeline) -> validation / rejection.

Pipeline files are never modified: corrections live in the database and are applied in memory
(display and regeneration). A regenerated clip is a NEW file (version n+1) that goes back to "to validate".
"""
from __future__ import annotations

import copy
import json
import shutil
import threading
import traceback
from pathlib import Path

from ..pipeline import clips as clipmod
from ..pipeline import translate, verses
from ..pipeline.output import SessionOutput
from . import db

# Clip statuses, in the order of the filter tabs. Protocol keys only: the front end translates them.
CLIP_STATUSES = ("a_valider", "valide", "publie", "rejete")
REQUEUE = "status = 'a_valider', decided_by = NULL, decided_at = NULL"


def corrections(conn, khutba_id: int) -> dict[tuple[int, str], dict]:
    return {(c["seg_index"], c["field"]): c for c in
            db.all_(conn, "SELECT * FROM corrections WHERE khutba_id = ?", khutba_id)}


def load(conn, k: dict) -> dict:
    """Transcription + verses + translations of the khutba, corrections applied."""
    o = SessionOutput(k["output_dir"])
    tr = o.read(o.transcript, {"segments": []})
    segs = copy.deepcopy(tr.get("segments", []))
    trans = o.read(o.translations, translate.empty())
    ms = o.read(o.verses, [])
    corr = corrections(conn, k["id"])
    for (i, field), c in corr.items():
        if i >= len(segs):
            continue
        if field == "text":
            segs[i]["text"] = c["value"]
            segs[i].pop("words", None)       # word-level timestamps lost: build_ass interpolates proportionally
        elif field == "fr":
            trans = translate.with_correction(trans, segs[i], c["value"])
    return {"segments": segs, "translations": trans, "verses": ms, "corrections": corr,
            "duration": tr.get("duration"), "model": tr.get("model"), "language": tr.get("language")}


def transcript_rows(data: dict, t0: float = 0.0, t1: float | None = None) -> list[dict]:
    """Rows to display: original text, French subtitle shown in the clips, verse if any, correctable field."""
    vms = verses.shown(data["verses"], ambiguous=True)
    rows = []
    for i, s in enumerate(data["segments"]):
        if t1 is not None and not (s["end"] > t0 and s["start"] < t1):
            continue
        sub = clipmod.subtitle(s, vms, data["translations"])
        m = sub.get("verse")
        rows.append({
            "i": i, "start": s["start"], "end": s["end"], "rel": max(0.0, s["start"] - t0), "text": s["text"],
            "arabic": sub["kind"] != "speech", "coran": sub["kind"] == "recitation",
            "verse": {"ref": m["ref"], "surah": m.get("surah_name", ""), "ambiguous": m.get("ambiguous", False),
                      "fr": sub["fr"]} if m else None,
            "fr": sub["fr"] if sub["kind"] in ("speech", "translation") else None,
            "field": {"speech": "text", "translation": "fr"}.get(sub["kind"]),
            "corrected": any((i, f) in data["corrections"] for f in ("text", "fr")),
        })
    return rows


def save_correction(conn, k: dict, seg_index: int, field: str, value: str, user: dict, ip=None) -> int:
    """Saves a correction; the clips covering this segment go back to "to validate" and must be regenerated.
    Returns the number of affected clips."""
    value = " ".join(value.split())
    data = load(conn, k)
    if not (0 <= seg_index < len(data["segments"])):
        raise ValueError("segment inconnu")
    row = next((r for r in transcript_rows(data) if r["i"] == seg_index), None)
    if row is None or row["field"] != field:
        raise ValueError("ce passage n'est pas corrigeable (verset : traduction Hamidullah fixe ; récitation non appariée : pas de sous-titre)")
    if not value:
        raise ValueError("texte vide")
    current = row["fr"] if field == "fr" else row["text"]
    if value == current:
        return 0
    old = db.one(conn, "SELECT * FROM corrections WHERE khutba_id = ? AND seg_index = ? AND field = ?", k["id"], seg_index, field)
    original = old["original"] if old else (current or "")
    conn.execute("INSERT INTO corrections (mosque_id, khutba_id, seg_index, field, original, value, user_id, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (khutba_id, seg_index, field) DO UPDATE SET "
                 "value = excluded.value, user_id = excluded.user_id, created_at = excluded.created_at",
                 (k["mosque_id"], k["id"], seg_index, field, original, value, user["id"], db.now_iso()))
    seg = data["segments"][seg_index]
    hit = db.all_(conn, "SELECT id, status FROM clips WHERE khutba_id = ? AND start < ? AND \"end\" > ? AND status != 'rejete'",
                  k["id"], seg["end"], seg["start"])
    for c in hit:
        if c["status"] == "publie":
            # already published: we do not rewrite history; the corrected version (v+1) goes through validation again
            conn.execute("UPDATE clips SET needs_regen = 1, updated_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
        else:
            conn.execute(f"UPDATE clips SET needs_regen = 1, {REQUEUE}, updated_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
    db.audit(conn, k["mosque_id"], user["id"], "correction." + ("sous-titre" if field == "text" else "traduction"),
             "khutba", k["id"], ip=ip, segment=seg_index, avant=current, apres=value, clips=[c["id"] for c in hit])
    return len(hit)


# ------------------------------------------------------------------ regeneration

def can_regenerate(clip: dict) -> tuple[bool, str]:
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg introuvable sur cette machine"
    if not clip.get("video_source") or not Path(clip["video_source"]).exists():
        return False, "vidéo source du clip introuvable"
    if clip.get("source_offset") is None:
        return False, "décalage source inconnu (manifest ancien)"
    return True, ""


def recover_interrupted(conn) -> None:
    """Regenerations in progress when the server stopped: they will never finish."""
    conn.execute("UPDATE clips SET regen_state = 'erreur', regen_error = 'interrompue (serveur arrêté)', updated_at = ? "
                 "WHERE regen_state = 'en_cours'", (db.now_iso(),))


def _render(db_path: str, clip_id: int, log=print) -> None:
    conn = db.connect(db_path)
    try:
        c = db.one(conn, "SELECT * FROM clips WHERE id = ?", clip_id)
        k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", c["khutba_id"])
        m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", c["mosque_id"])
        data = load(conn, k)
        ver = c["version"] + 1
        out = clipmod.make_clip(data["segments"], data["verses"], data["translations"],
                                {"start": c["start"], "end": c["end"], "kind": c["kind"]}, c["idx"], ver,
                                c["video_source"], c["source_offset"], Path(c["file_path"]).parent,
                                {"title": m["clip_title"], "subtitle": ""}, json.loads(c["crop"]) if c["crop"] else None, log)
        conn.execute("UPDATE clips SET file_path = ?, version = ?, needs_regen = 0, regen_state = '', regen_error = '', "
                     f"{REQUEUE}, updated_at = ? WHERE id = ?", (str(out), ver, db.now_iso(), clip_id))
        db.audit(conn, c["mosque_id"], None, "clip.regenere", "clip", clip_id, version=ver, fichier=out.name)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        conn.execute("UPDATE clips SET regen_state = 'erreur', regen_error = ?, updated_at = ? WHERE id = ?",
                     (str(e)[:500], db.now_iso(), clip_id))
    finally:
        conn.close()


def start_regeneration(conn, db_path: str, clip: dict, user: dict, ip=None, background: bool = True) -> None:
    if not (clip["needs_regen"] or clip["regen_state"] == "erreur"):
        raise ValueError("aucune correction à appliquer : le clip est déjà validé")
    ok, why = can_regenerate(clip)
    if not ok:
        raise ValueError(f"régénération impossible : {why}")
    if clip["regen_state"] == "en_cours":
        raise ValueError("régénération déjà en cours")
    conn.execute("UPDATE clips SET regen_state = 'en_cours', regen_error = '', updated_at = ? WHERE id = ?", (db.now_iso(), clip["id"]))
    db.audit(conn, clip["mosque_id"], user["id"], "clip.regeneration", "clip", clip["id"], ip=ip, version=clip["version"] + 1)
    if background:
        threading.Thread(target=_render, args=(db_path, clip["id"]), daemon=True, name=f"regen-{clip['id']}").start()
    else:
        _render(db_path, clip["id"])


# ------------------------------------------------------------------ decision

def can_validate(clip: dict) -> tuple[bool, str]:
    if clip["needs_regen"] or clip["regen_state"] == "en_cours":
        return False, "des corrections ne sont pas encore dans la vidéo : régénérer le clip avant de le valider"
    if not Path(clip["file_path"]).exists():
        return False, "fichier du clip introuvable"
    return True, ""


def decide(conn, clip: dict, decision: str, user: dict, reason: str = "", ip=None) -> None:
    if decision not in ("valide", "rejete"):
        raise ValueError("décision inconnue")
    if clip["status"] == "publie":
        raise ValueError("clip déjà publié : décision figée")
    if decision == "valide":
        ok, why = can_validate(clip)
        if not ok:
            raise ValueError(why)
    conn.execute("UPDATE clips SET status = ?, decided_by = ?, decided_at = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
                 (decision, user["id"], db.now_iso(), reason.strip()[:500] if decision == "rejete" else "", db.now_iso(), clip["id"]))
    db.audit(conn, clip["mosque_id"], user["id"], "clip." + ("valide" if decision == "valide" else "rejete"), "clip", clip["id"],
             ip=ip, version=clip["version"], motif=reason.strip()[:500] or None)


def reopen(conn, clip: dict, user: dict, ip=None) -> None:
    if clip["status"] not in ("valide", "rejete"):
        raise ValueError("seul un clip validé ou rejeté peut être remis en file")
    conn.execute(f"UPDATE clips SET {REQUEUE}, reject_reason = '', updated_at = ? WHERE id = ?", (db.now_iso(), clip["id"]))
    db.audit(conn, clip["mosque_id"], user["id"], "clip.remis_en_file", "clip", clip["id"], ip=ip)
