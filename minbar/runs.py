"""Run de l'orchestrateur : data/state/<run_id>/sessions.json.

Écrit par l'orchestrateur (une entrée par session : prévue, manuelle ou janaza), lu par le pipeline et le
back-office. Les sorties du pipeline d'une session vont dans data/output/<run_id>/<session_dir(index)>/.
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
    """Numéro affiché d'une session (index 0 -> session 1)."""
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
    """sessions.json du run le plus récent."""
    runs = sorted((d for d in state_dir.glob("*") if RUN_RE.match(d.name) and (d / SESSIONS).exists()),
                  key=lambda d: d.name)
    return runs[-1] / SESSIONS if runs else None


def is_khutba(s: dict) -> bool:
    """La Janaza n'a aucun contenu dérivé (ni transcription, ni clips, ni import)."""
    return s.get("status") != "janaza"


def is_done(s: dict) -> bool:
    """Session terminée : début et fin connus, prête pour le pipeline."""
    return s.get("status") == "done"


def sources(data: dict, s: dict) -> tuple[str | None, str | None]:
    """(enregistrement du live, enregistrement propre) qui contiennent la session.

    Les positions start_pos / end_pos d'une session se lisent dans l'enregistrement n° `recording` (l'encodeur
    en ouvre un nouveau à chaque relance). Runs anciens sans ce champ : le dernier enregistrement."""
    recs = data.get("recordings") or []
    cleans = data.get("recordings_clean") or []
    i = s.get("recording")
    if i is None:
        i = len(recs) - 1
    if not 0 <= i < len(recs):
        return None, None
    clean = cleans[i] if len(cleans) == len(recs) else None
    return recs[i], clean
