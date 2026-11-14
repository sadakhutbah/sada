"""Per-mosque processing files: clip count (`pipeline.json`) and language model (`llm.json`).

Each mosque gets `data/mosques/<slug>/<name>.json`. While that file does not exist yet, the
process-wide file is read (an instance created before multi-mosque keeps its settings); the first
save then writes the mosque's own file. An explicit `SADA_PIPELINE_CONFIG` / `SADA_LLM_CONFIG`
still wins, as everywhere else: it pins one file for the whole process.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import paths
from ..pipeline import llm
from ..pipeline import settings as pipeline_settings

FILES = {
    "pipeline": ("SADA_PIPELINE_CONFIG", pipeline_settings.config_path),
    "llm": ("SADA_LLM_CONFIG", llm.config_path),
}


def own_path(mosque: dict, name: str) -> Path:
    return paths.DATA / "mosques" / mosque["slug"] / f"{name}.json"


def write_path(mosque: dict, name: str) -> Path:
    var, shared = FILES[name]
    return shared() if os.environ.get(var) else own_path(mosque, name)


def read_path(mosque: dict, name: str) -> Path:
    var, shared = FILES[name]
    own = write_path(mosque, name)
    return own if own.exists() or os.environ.get(var) else shared()


def pipeline_env(mosque: dict) -> dict:
    """Environment for a pipeline run of this mosque: both files point at the mosque's own."""
    return {var: str(read_path(mosque, name)) for name, (var, _) in FILES.items()}
