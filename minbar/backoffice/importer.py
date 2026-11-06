"""Import des runs du pipeline : data/state/<run>/sessions.json + data/output/<run>/session*/ -> khutbas + clips.

Idempotent : une khutba est identifiée par (mosquée, run, session) et un clip par (khutba, numéro) ; relancer
l'import n'écrase ni les décisions de validation ni les corrections. Les sessions Janaza (exclues du pipeline)
ne sont jamais importées.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import db

RUN_RE = re.compile(r"^\d{8}_\d{6}$")


def _theme(notes: Path) -> str:
    if not notes.exists():
        return ""
    lines = notes.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Thème"):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return nxt.strip()[:300]
    return ""


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


def _clip_idx(name: str, default: int) -> int:
    m = re.match(r"clip(\d+)_", name)
    return int(m.group(1)) if m else default


def import_run(conn, mosque: dict, run_id: str, extract_video: bool = True, log=print) -> dict:
    dirs = db.mosque_dirs(mosque)
    out = dirs["output"] / run_id
    if not RUN_RE.match(run_id) or not out.is_dir():
        raise FileNotFoundError(f"run introuvable : {out}")
    sj = dirs["state"] / run_id / "sessions.json"
    data = json.loads(sj.read_text()) if sj.exists() else {}
    recs = {s["index"]: s for s in data.get("sessions", [])}
    rec_path = (data.get("recordings") or [None])[-1]
    date = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    res = {"run": run_id, "khutbas_new": 0, "clips_new": 0, "skipped": []}
    for sdir in sorted(out.glob("session*")):
        m = re.fullmatch(r"session(\d+)", sdir.name)
        if not m or not (sdir / "transcript.json").exists():
            continue
        idx = int(m.group(1)) - 1
        s = recs.get(idx, {})
        if s.get("exclude_from_pipeline") or s.get("status") == "janaza":
            res["skipped"].append(sdir.name)
            continue
        tr = json.loads((sdir / "transcript.json").read_text())
        verses = json.loads((sdir / "verses.json").read_text()) if (sdir / "verses.json").exists() else []
        start, end = s.get("start_pos"), s.get("end_pos")
        duration = (end - start) if (start is not None and end is not None) else tr.get("duration")
        planned = s.get("planned") or ""
        from .content import verses_for_display      # même règle que l'affichage : formules rituelles exclues
        n_verses = len(verses_for_display(verses))
        k = db.one(conn, "SELECT * FROM khutbas WHERE mosque_id = ? AND run_id = ? AND session_index = ?",
                   mosque["id"], run_id, idx)
        if not k:
            label = f"Session {idx + 1}" + (f" — {planned.replace(':', ' h ')}" if planned else "")
            cur = conn.execute(
                "INSERT INTO khutbas (mosque_id, run_id, session_index, label, planned, date, start_clock, end_clock, duration_s,"
                " imam_id, theme, output_dir, recording, start_pos, end_pos, verses_count, imported_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mosque["id"], run_id, idx, label, planned, date, s.get("start_clock"), s.get("end_clock"), duration,
                 mosque.get("default_imam_id"), _theme(sdir / "notes.md"), str(sdir), rec_path, start, end,
                 n_verses, db.now_iso()))
            k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", cur.lastrowid)
            res["khutbas_new"] += 1
            db.audit(conn, mosque["id"], None, "import.khutba", "khutba", k["id"], run=run_id, session=idx + 1)
        elif k["verses_count"] != n_verses:            # champ dérivé : rafraîchi (aucune donnée humaine touchée)
            conn.execute("UPDATE khutbas SET verses_count = ? WHERE id = ?", (n_verses, k["id"]))
        if extract_video and not (k["video_path"] and Path(k["video_path"]).exists()):
            v = _extract_video(rec_path, start, end, sdir / "khutba.mp4", log)
            if v:
                conn.execute("UPDATE khutbas SET video_path = ? WHERE id = ?", (v, k["id"]))
        man = sdir / "clips" / "manifest.json"
        for n, c in enumerate(json.loads(man.read_text()) if man.exists() else [], 1):
            ci = _clip_idx(c["file"], n)
            if db.one(conn, "SELECT id FROM clips WHERE khutba_id = ? AND idx = ?", k["id"], ci):
                continue
            t = db.now_iso()
            src_off = c.get("source_offset_s")
            cur = conn.execute(
                "INSERT INTO clips (mosque_id, khutba_id, idx, kind, why, start, \"end\", file_path, video_source, source_offset,"
                " crop, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mosque["id"], k["id"], ci, c.get("kind", ""), c.get("why", ""), c["start"], c["end"],
                 str(sdir / "clips" / c["file"]), c.get("video_source"),
                 (src_off - c["start"]) if src_off is not None else None,
                 json.dumps(c["crop"]) if c.get("crop") else None, t, t))
            res["clips_new"] += 1
            db.audit(conn, mosque["id"], None, "import.clip", "clip", cur.lastrowid, file=c["file"])
    return res


def scan(conn, mosque: dict, extract_video: bool = True, log=print) -> list[dict]:
    """Importe tous les runs présents dans le dossier de sortie de la mosquée."""
    out = db.mosque_dirs(mosque)["output"]
    results = []
    for d in sorted(out.glob("*")) if out.is_dir() else []:
        if d.is_dir() and RUN_RE.match(d.name) and any(d.glob("session*/transcript.json")):
            try:
                results.append(import_run(conn, mosque, d.name, extract_video, log))
            except Exception as e:  # noqa: BLE001
                log(f"import {d.name} : {e}")
    return results
