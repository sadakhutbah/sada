"""Import des runs du pipeline : data/state/<run>/sessions.json + data/output/<run>/session*/ -> khutbas + clips.

Idempotent : une khutba est identifiée par (mosquée, run, session) et un clip par (khutba, numéro) ; relancer
l'import n'écrase ni les décisions de validation ni les corrections. Les sessions Janaza (exclues du pipeline)
ne sont jamais importées.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .. import runs
from ..pipeline import notes, verses as verses_mod
from ..pipeline.output import SessionOutput
from . import db


def _theme(path: Path) -> str:
    return notes.theme_from_markdown(path.read_text(encoding="utf-8")) if path.exists() else ""


def _extract_video(rec: str | None, start: float | None, end: float | None, out: Path, log) -> str | None:
    """Extrait la session de l'enregistrement du live (copie des flux, sans réencodage) en MP4 lisible sur téléphone."""
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    if not rec or start is None or not Path(rec).exists() or not shutil.which("ffmpeg"):
        return None
    dur = (end - start) if end else None
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-i", rec]
    cmd += (["-t", f"{dur:.2f}"] if dur else []) + ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                                                     "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=600, capture_output=True)
        log(f"vidéo de la session -> {out}")
        return str(out)
    except Exception as e:  # noqa: BLE001
        log(f"extraction vidéo impossible ({rec}) : {e}")
        out.unlink(missing_ok=True)
        return None


def import_run(conn, mosque: dict, run_id: str, extract_video: bool = True, log=print) -> dict:
    dirs = db.mosque_dirs(mosque)
    out = dirs["output"] / run_id
    if not runs.RUN_RE.match(run_id) or not out.is_dir():
        raise FileNotFoundError(f"run introuvable : {out}")
    sj = dirs["state"] / run_id / runs.SESSIONS
    data = runs.load(sj) if sj.exists() else {}
    recs = {s["index"]: s for s in data.get("sessions", [])}
    date = runs.run_date(run_id)
    res = {"run": run_id, "khutbas_new": 0, "clips_new": 0, "skipped": []}
    for sdir in sorted(out.glob("session*")):
        idx = runs.session_index(sdir.name)
        o = SessionOutput(sdir)
        if idx is None or not o.is_transcribed():
            continue
        s = recs.get(idx, {})
        if not runs.is_khutba(s):
            res["skipped"].append(sdir.name)
            continue
        rec_path, _ = runs.sources(data, s)
        tr = o.read(o.transcript, {})
        verses = o.read(o.verses, [])
        start, end = s.get("start_pos"), s.get("end_pos")
        duration = (end - start) if (start is not None and end is not None) else tr.get("duration")
        planned = s.get("planned") or ""
        n_verses = len(verses_mod.for_display(verses))
        k = db.one(conn, "SELECT * FROM khutbas WHERE mosque_id = ? AND run_id = ? AND session_index = ?",
                   mosque["id"], run_id, idx)
        if not k:
            label = f"Session {idx + 1}" + (f" — {planned.replace(':', ' h ')}" if planned else "")
            cur = conn.execute(
                "INSERT INTO khutbas (mosque_id, run_id, session_index, label, planned, date, start_clock, end_clock, duration_s,"
                " imam_id, theme, output_dir, recording, start_pos, end_pos, verses_count, imported_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mosque["id"], run_id, idx, label, planned, date, s.get("start_clock"), s.get("end_clock"), duration,
                 mosque.get("default_imam_id"), _theme(o.notes), str(sdir), rec_path, start, end,
                 n_verses, db.now_iso()))
            k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", cur.lastrowid)
            res["khutbas_new"] += 1
            db.audit(conn, mosque["id"], None, "import.khutba", "khutba", k["id"], run=run_id, session=idx + 1)
        elif k["verses_count"] != n_verses:            # champ dérivé : rafraîchi (aucune donnée humaine touchée)
            conn.execute("UPDATE khutbas SET verses_count = ? WHERE id = ?", (n_verses, k["id"]))
        if extract_video and not (k["video_path"] and Path(k["video_path"]).exists()):
            v = _extract_video(rec_path, start, end, o.video, log)
            if v:
                conn.execute("UPDATE khutbas SET video_path = ? WHERE id = ?", (v, k["id"]))
        for c in o.manifest_entries():
            ci = c["idx"]
            if db.one(conn, "SELECT id FROM clips WHERE khutba_id = ? AND idx = ?", k["id"], ci):
                continue
            t = db.now_iso()
            cur = conn.execute(
                "INSERT INTO clips (mosque_id, khutba_id, idx, kind, why, start, \"end\", file_path, video_source, source_offset,"
                " crop, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mosque["id"], k["id"], ci, c["kind"], c["why"], c["start"], c["end"],
                 str(o.clips / c["file"]), c["video_source"], c["source_offset"],
                 json.dumps(c["crop"]) if c.get("crop") else None, t, t))
            res["clips_new"] += 1
            db.audit(conn, mosque["id"], None, "import.clip", "clip", cur.lastrowid, file=c["file"])
    return res


def scan(conn, mosque: dict, extract_video: bool = True, log=print) -> list[dict]:
    """Importe tous les runs présents dans le dossier de sortie de la mosquée."""
    out = db.mosque_dirs(mosque)["output"]
    results = []
    for d in sorted(out.glob("*")) if out.is_dir() else []:
        if d.is_dir() and runs.RUN_RE.match(d.name) and any(SessionOutput(x).is_transcribed() for x in d.glob("session*")):
            try:
                results.append(import_run(conn, mosque, d.name, extract_video, log))
            except Exception as e:  # noqa: BLE001
                log(f"import {d.name} : {e}")
    return results
