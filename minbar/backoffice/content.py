"""Contenu d'une khutba (transcription corrigée, versets, traductions) et cycle de vie des clips :
corrections -> régénération (pipeline clips.build_ass + render_clip) -> validation / rejet.

Les fichiers du pipeline ne sont jamais modifiés : les corrections vivent en base et sont appliquées en mémoire
(affichage et régénération). Un clip régénéré est un NOUVEAU fichier (version n+1) qui repasse « à valider ».
"""
from __future__ import annotations

import copy
import json
import threading
import traceback
from pathlib import Path

from ..pipeline.arabic import arabic_ratio
from ..pipeline.translate import _key, is_formula, match_for, verse_matches
from . import db

STATUS_LABELS = {"a_valider": "À valider", "valide": "Validé", "rejete": "Rejeté", "publie": "Publié"}


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def corrections(conn, khutba_id: int) -> dict[tuple[int, str], dict]:
    return {(c["seg_index"], c["field"]): c for c in
            db.all_(conn, "SELECT * FROM corrections WHERE khutba_id = ?", khutba_id)}


def load(conn, k: dict) -> dict:
    """Transcription + versets + traductions de la khutba, corrections appliquées."""
    d = Path(k["output_dir"])
    tr = _read(d / "transcript.json", {"segments": []})
    segs = copy.deepcopy(tr.get("segments", []))
    trans = _read(d / "translations.json", {"seg": {}, "verse_fr": {}})
    trans.setdefault("seg", {})
    trans.setdefault("verse_fr", {})
    ms = _read(d / "verses.json", [])
    corr = corrections(conn, k["id"])
    for (i, field), c in corr.items():
        if i >= len(segs):
            continue
        if field == "text":
            segs[i]["text"] = c["value"]
            segs[i].pop("words", None)       # horodatage par mot perdu : build_ass interpole au prorata
        elif field == "fr":
            trans["seg"][_key(segs[i]["text"])] = {"fr": c["value"], "coran": False}
    return {"segments": segs, "translations": trans, "verses": ms, "corrections": corr,
            "duration": tr.get("duration"), "model": tr.get("model"), "language": tr.get("language")}


def verses_for_display(ms: list[dict]) -> list[dict]:
    """Versets à montrer : détections sûres/courtes (formules rituelles exclues) + passages ambigus signalés."""
    sure = [m for m in verse_matches(ms) if not m.get("ambiguous")]
    keys = {(m["ref"], m["start"]) for m in sure}
    amb = [{**m, "ambiguous": True} for m in ms
           if (m["ref"], m["start"]) not in keys and m.get("confidence") == "ambigu" and not is_formula(m.get("heard", ""))]
    return sorted(sure + amb, key=lambda m: m["start"])


def transcript_rows(data: dict, t0: float = 0.0, t1: float | None = None) -> list[dict]:
    """Lignes affichables : texte d'origine, sous-titre français affiché, verset éventuel, champ corrigeable."""
    vms = verse_matches(data["verses"], ambiguous=True)
    rows = []
    for i, s in enumerate(data["segments"]):
        if t1 is not None and not (s["end"] > t0 and s["start"] < t1):
            continue
        ar = arabic_ratio(s["text"]) > 0.5
        row = {"i": i, "start": s["start"], "end": s["end"], "rel": max(0.0, s["start"] - t0), "text": s["text"],
               "arabic": ar, "verse": None, "fr": None, "field": None, "coran": False,
               "corrected": any((i, f) in data["corrections"] for f in ("text", "fr"))}
        if ar:
            m = match_for(s, vms)
            if m:
                row["verse"] = {"ref": m["ref"], "surah": m.get("surah_name", ""), "ambiguous": m.get("ambiguous", False),
                                "fr": data["translations"]["verse_fr"].get(f"{m['ref']}@{m['start']}") or m.get("verse_fr", "")}
            else:
                e = data["translations"]["seg"].get(_key(s["text"]))
                row["coran"] = bool(e and e.get("coran"))
                row["fr"] = e["fr"] if e and not e.get("coran") else None
                row["field"] = None if row["coran"] else "fr"
        else:
            row["fr"] = s["text"]
            row["field"] = "text"
        rows.append(row)
    return rows


def save_correction(conn, k: dict, seg_index: int, field: str, value: str, user: dict, ip=None) -> int:
    """Enregistre une correction ; les clips qui couvrent ce segment repassent « à valider » et doivent être régénérés.
    Renvoie le nombre de clips touchés."""
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
            # déjà publié : on ne réécrit pas l'histoire ; la version corrigée (v+1) repassera par la validation
            conn.execute("UPDATE clips SET needs_regen = 1, updated_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
        else:
            conn.execute("UPDATE clips SET needs_regen = 1, status = 'a_valider', decided_by = NULL, decided_at = NULL, "
                         "updated_at = ? WHERE id = ?", (db.now_iso(), c["id"]))
    db.audit(conn, k["mosque_id"], user["id"], "correction." + ("sous-titre" if field == "text" else "traduction"),
             "khutba", k["id"], ip=ip, segment=seg_index, avant=current, apres=value, clips=[c["id"] for c in hit])
    return len(hit)


# ------------------------------------------------------------------ régénération

def can_regenerate(clip: dict) -> tuple[bool, str]:
    import shutil
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg introuvable sur cette machine"
    if not clip.get("video_source") or not Path(clip["video_source"]).exists():
        return False, "vidéo source du clip introuvable"
    if clip.get("source_offset") is None:
        return False, "décalage source inconnu (manifest ancien)"
    return True, ""


def _render(db_path: str, clip_id: int, log=print) -> None:
    from ..pipeline import clips as clipmod
    conn = db.connect(db_path)
    try:
        c = db.one(conn, "SELECT * FROM clips WHERE id = ?", clip_id)
        k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", c["khutba_id"])
        m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", c["mosque_id"])
        data = load(conn, k)
        ver = c["version"] + 1
        cdir = Path(c["file_path"]).parent
        ass = cdir / f"clip{c['idx']}_v{ver}.ass"
        out = cdir / f"clip{c['idx']}_{c['kind'] or 'extrait'}_v{ver}_A-VALIDER.mp4"
        w = {"start": c["start"], "end": c["end"], "kind": c["kind"]}
        clipmod.build_ass(data["segments"], data["verses"], c["start"], c["end"], str(ass), tr=data["translations"],
                          header={"title": m["clip_title"], "subtitle": ""})
        clipmod.render_clip(c["video_source"], c["source_offset"], w, ass.name, str(out), log,
                            crop=json.loads(c["crop"]) if c["crop"] else None)
        conn.execute("UPDATE clips SET file_path = ?, version = ?, needs_regen = 0, regen_state = '', regen_error = '', "
                     "status = 'a_valider', decided_by = NULL, decided_at = NULL, updated_at = ? WHERE id = ?", (str(out), ver, db.now_iso(), clip_id))
        db.audit(conn, c["mosque_id"], None, "clip.regenere", "clip", clip_id, version=ver, fichier=out.name)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        conn.execute("UPDATE clips SET regen_state = 'erreur', regen_error = ?, updated_at = ? WHERE id = ?",
                     (str(e)[:500], db.now_iso(), clip_id))
    finally:
        conn.close()


def start_regeneration(conn, db_path: str, clip: dict, user: dict, ip=None, background: bool = True) -> None:
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


# ------------------------------------------------------------------ décision

def decide(conn, clip: dict, decision: str, user: dict, reason: str = "", ip=None) -> None:
    if decision not in ("valide", "rejete"):
        raise ValueError("décision inconnue")
    if clip["status"] == "publie":
        raise ValueError("clip déjà publié : décision figée")
    if decision == "valide":
        if clip["needs_regen"] or clip["regen_state"] == "en_cours":
            raise ValueError("des corrections ne sont pas encore dans la vidéo : régénérer le clip avant de le valider")
        if not Path(clip["file_path"]).exists():
            raise ValueError("fichier du clip introuvable")
    conn.execute("UPDATE clips SET status = ?, decided_by = ?, decided_at = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
                 (decision, user["id"], db.now_iso(), reason.strip()[:500] if decision == "rejete" else "", db.now_iso(), clip["id"]))
    db.audit(conn, clip["mosque_id"], user["id"], "clip." + ("valide" if decision == "valide" else "rejete"), "clip", clip["id"],
             ip=ip, version=clip["version"], motif=reason.strip()[:500] or None)


def reopen(conn, clip: dict, user: dict, ip=None) -> None:
    if clip["status"] not in ("valide", "rejete"):
        raise ValueError("seul un clip validé ou rejeté peut être remis en file")
    conn.execute("UPDATE clips SET status = 'a_valider', decided_by = NULL, decided_at = NULL, reject_reason = '', updated_at = ? "
                 "WHERE id = ?", (db.now_iso(), clip["id"]))
    db.audit(conn, clip["mosque_id"], user["id"], "clip.remis_en_file", "clip", clip["id"], ip=ip)
