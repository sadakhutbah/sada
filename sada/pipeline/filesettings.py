"""File-backed settings shared by the pipeline modules (model, transcription, post-processing).

Each module keeps a small JSON file written by the back-office and read by the pipeline. The rules
are the same everywhere: the file location can be moved with an environment variable, a missing or
unreadable file reads as `{}`, writes are atomic, and a value is resolved as
argument > process setting > environment > file > default (`sada.config.pick`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import paths
from ..config import pick


class FileSettings:
    """A JSON settings file: location, tolerant read, atomic write, precedence helper."""

    def __init__(self, env_var: str, filename: str, keys: tuple[str, ...] | None = None, private: bool = False):
        self.env_var = env_var
        self.filename = filename
        self.keys = keys  # when set, only these keys are read back
        self.private = private  # file holds a secret: mode 600

    def path(self) -> Path:
        """`$env_var` if set, otherwise `<data dir>/<filename>` (data dir looked up at call time)."""
        return Path(os.environ.get(self.env_var) or paths.DATA / self.filename)

    def read(self, path: Path | None = None) -> dict:
        """Saved settings. File missing, unreadable or not an object: {}."""
        try:
            d = json.loads((path or self.path()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(d, dict):
            return {}
        return {k: d[k] for k in self.keys if k in d} if self.keys is not None else d

    def write(self, data: dict, path: Path | None = None) -> None:
        """Atomic write (temporary file, then rename)."""
        p = path or self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        if self.private:
            tmp.chmod(0o600)
        os.replace(tmp, p)

    @staticmethod
    def value(*, cli=None, override=None, env: str | None = None, cfg=None, default=None):
        """First set value of: argument, process setting, environment variable `env`, file value `cfg`, default."""
        return pick(cli, override, os.environ.get(env) if env else None, cfg, default)
