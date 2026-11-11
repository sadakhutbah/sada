"""Orchestrator run: data/state/<run_id>/sessions.json.

Written by the orchestrator (one entry per session: planned, manual or janaza), read by the pipeline and the
back-office. The pipeline outputs of a session go to data/output/<run_id>/<session_dir(index)>/.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from . import paths

SESSIONS = "sessions.json"
RUN_RE = re.compile(r"^\d{8}_\d{6}$")
_SESSION_DIR = re.compile(r"session(\d+)")


def new_run_id(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y%m%d_%H%M%S")


def run_date(run_id: str) -> str:
    return f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"


def session_number(index: int) -> int:
    """Displayed number of a session (index 0 -> session 1)."""
    return index + 1


def session_dir(index: int) -> str:
    return f"session{session_number(index)}"


def session_index(dirname: str) -> int | None:
    m = _SESSION_DIR.fullmatch(dirname)
    return int(m.group(1)) - 1 if m else None


def write(run_dir: Path, data: dict) -> None:
    (run_dir / SESSIONS).write_text(json.dumps(data, ensure_ascii=False, indent=1))


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def latest(state_dir: Path = paths.STATE) -> Path | None:
    """sessions.json of the most recent run."""
    runs = sorted((d for d in state_dir.glob("*") if RUN_RE.match(d.name) and (d / SESSIONS).exists()),
                  key=lambda d: d.name)
    return runs[-1] / SESSIONS if runs else None


def produces_content(s: dict) -> bool:
    """The session feeds the pipeline (transcription, clips, import): everything but the Janaza."""
    return s.get("status") != "janaza"


def is_done(s: dict) -> bool:
    """Finished session: start and end known, ready for the pipeline."""
    return s.get("status") == "done"


def sources(data: dict, s: dict) -> tuple[str | None, str | None]:
    """(live recording, clean recording) that contain the session.

    The start_pos / end_pos positions of a session are read from recording no. `recording` (the encoder
    opens a new one on each restart). Older runs without this field: the last recording."""
    recs = data.get("recordings") or []
    cleans = data.get("recordings_clean") or []
    i = s.get("recording")
    if i is None:
        i = len(recs) - 1
    if not 0 <= i < len(recs):
        return None, None
    clean = cleans[i] if len(cleans) == len(recs) else None
    return recs[i], clean
