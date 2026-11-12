"""Import of the pipeline runs: data/state/<run>/sessions.json + data/output/<run>/session*/ -> khutbas + clips.

Idempotent: a khutba is identified by (mosque, run, session) and a clip by (khutba, number); re-running
the import overwrites neither validation decisions nor corrections. Janaza sessions (excluded from the pipeline)
are never imported.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .. import runs
from ..events import kind_label
from ..i18n import _
from ..pipeline import notes
from ..pipeline import verses as verses_mod
from ..pipeline.output import SessionOutput
from . import db


def _theme(path: Path) -> str:
    return notes.theme_from_markdown(path.read_text(encoding="utf-8")) if path.exists() else ""


def _ffmpeg() -> str | None:
    """ffmpeg from PATH, otherwise the copy shipped next to the app (tools/bin) by the all-in-one install."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # A user service does not inherit the login PATH: also look in the usual user folder.
    for bin_ in (Path(__file__).resolve().parents[2] / "tools" / "bin", Path.home() / ".local" / "bin"):
        for name in ("ffmpeg.exe", "ffmpeg"):
            if (bin_ / name).exists():
                return str(bin_ / name)
    return None


def _extract_video(rec: str | None, start: float | None, end: float | None, out: Path, log) -> str | None:
    """Extracts the session from the live recording (stream copy, no re-encoding) into an MP4 playable on a phone."""
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    ffmpeg = _ffmpeg()
    if not rec or start is None or not Path(rec).exists() or not ffmpeg:
        log(_("video extraction impossible ({recording}): ffmpeg or recording missing", recording=rec))
        return None
    dur = (end - start) if end else None
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-i", rec]
    cmd += (["-t", f"{dur:.2f}"] if dur else []) + ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                                                     "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=600, capture_output=True)
        log(_("session video -> {path}", path=out))
        return str(out)
    except Exception as e:  # noqa: BLE001
        log(_("video extraction impossible ({recording}): {error}", recording=rec, error=e))
        out.unlink(missing_ok=True)
        return None


def khutba_label(session: dict) -> str:
    """Readable label of a session: "Dars — title · session 7", otherwise "Session 1 — 12 h 30"."""
    kind = session.get("kind") or "khutba"
    title = (session.get("title") or "").strip()
    if kind != "khutba" and title:
        label = f"{kind_label(kind)} — {title}"
        if session.get("session_no"):
            label += f" · séance {session['session_no']}"
        return label
    planned = session.get("planned") or ""
    return f"Session {int(session.get('index', 0)) + 1}" + (f" — {planned.replace(':', ' h ')}" if planned else "")


def import_run(conn, mosque: dict, run_id: str, extract_video: bool = True, log=print) -> dict:
    dirs = db.mosque_dirs(mosque)
    out = dirs["output"] / run_id
    if not runs.RUN_RE.match(run_id) or not out.is_dir():
        raise FileNotFoundError(_("run not found: {path}", path=out))
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
        if not runs.produces_content(s):
            res["skipped"].append(sdir.name)
            continue
        rec_path, _sources = runs.sources(data, s)
        tr = o.read(o.transcript, {})
        verses = o.read(o.verses, [])
        start, end = s.get("start_pos"), s.get("end_pos")
        duration = (end - start) if (start is not None and end is not None) else tr.get("duration")
        planned = s.get("planned") or ""
        n_verses = len(verses_mod.for_display(verses))
        k = db.one(conn, "SELECT * FROM khutbas WHERE mosque_id = ? AND run_id = ? AND session_index = ?",
                   mosque["id"], run_id, idx)
        if not k:
            label = khutba_label(dict(s, index=idx))
            kind = s.get("kind") or "khutba"
            cur = conn.execute(
                "INSERT INTO khutbas (mosque_id, run_id, session_index, label, planned, date, start_clock, end_clock, duration_s,"
                " imam_id, speaker, theme, output_dir, recording, start_pos, end_pos, verses_count, kind, title, series, session_no, imported_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mosque["id"], run_id, idx, label, planned, date, s.get("start_clock"), s.get("end_clock"), duration,
                 mosque.get("default_imam_id"), (s.get("speaker") or "").strip(), _theme(o.notes), str(sdir), rec_path, start, end,
                 n_verses, kind, s.get("title") or "", s.get("series") or "", s.get("session_no"),
                 db.now_iso()))
            k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", cur.lastrowid)
            res["khutbas_new"] += 1
            db.audit(conn, mosque["id"], None, "import.khutba", "khutba", k["id"], run=run_id, session=runs.session_number(idx))
        else:
            if k["verses_count"] != n_verses:          # derived field: refreshed (no human data touched)
                conn.execute("UPDATE khutbas SET verses_count = ? WHERE id = ?", (n_verses, k["id"]))
            # La nature (khutba / dars / conférence) vient de sessions.json : une séance importée avant
            # que l'orchestrateur la renseigne, ou avant la colonne, resterait sinon « khutba » et
            # apparaîtrait dans la mauvaise liste. Le libellé n'est recalculé que s'il était automatique.
            kind = s.get("kind") or "khutba"
            if (k["kind"] or "khutba") != kind:
                ancien = khutba_label(dict(s, index=idx, kind=k["kind"] or "khutba", title=k["title"] or ""))
                label = khutba_label(dict(s, index=idx)) if k["label"] == ancien else k["label"]
                conn.execute("UPDATE khutbas SET kind = ?, title = ?, series = ?, session_no = ?, label = ? WHERE id = ?",
                             (kind, k["title"] or s.get("title") or "", k["series"] or s.get("series") or "",
                              k["session_no"] if k["session_no"] is not None else s.get("session_no"), label, k["id"]))
                k = db.one(conn, "SELECT * FROM khutbas WHERE id = ?", k["id"])
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
    """Imports every run present in the mosque's output folder."""
    out = db.mosque_dirs(mosque)["output"]
    results = []
    for d in sorted(out.glob("*")) if out.is_dir() else []:
        if d.is_dir() and runs.RUN_RE.match(d.name) and any(SessionOutput(x).is_transcribed() for x in d.glob("session*")):
            try:
                results.append(import_run(conn, mosque, d.name, extract_video, log))
            except Exception as e:  # noqa: BLE001
                log(_("import {run}: {error}", run=d.name, error=e))
    return results
