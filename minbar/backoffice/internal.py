"""Handshake between the two local processes (orchestrator and back-office).

The orchestrator used to read the back-office SQLite file directly to find the notification
settings: a coupling on another process's schema, plus a port heuristic to guess the mosque.
It now asks the back-office over HTTP, authenticated by a token both processes read from the same
file. Nothing leaves the machine.

The token file is created (mode 0600) by whichever process needs it first.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from .. import paths

HEADER = "X-Internal-Token"


def token(path: str | Path | None = None) -> str:
    """Internal token, created on first use: a local secret, never shown in an interface."""
    p = Path(path or os.environ.get("MINBAR_INTERNAL_TOKEN_FILE") or paths.INTERNAL_TOKEN)
    try:
        t = p.read_text().strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # atomic: two processes may race
    except FileExistsError:
        return p.read_text().strip()
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32))
    return p.read_text().strip()


def check(given: str | None, path: str | Path | None = None) -> bool:
    """Is the token presented by the caller the local one? (constant-time comparison)"""
    return bool(given) and hmac.compare_digest(str(given), token(path))
