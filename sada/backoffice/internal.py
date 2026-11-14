"""Handshake between the two local processes (orchestrator and back-office).

The orchestrator used to read the back-office SQLite file directly to find the notification
settings: a coupling on another process's schema, plus a port heuristic to guess the mosque.
It now asks the back-office over HTTP, authenticated by a token both processes read from the same
file. Nothing leaves the machine.

The token file is created (mode 0600) by whichever process needs it first.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from .. import paths

HEADER = "X-Internal-Token"


AGENT_ENV = "SADA_AGENT_TOKEN"


def token(path: str | Path | None = None) -> str:
    """Token the orchestrator presents: its mosque's agent token when one is set (hosted
    back-office, `SADA_AGENT_TOKEN`), otherwise the local one, created on first use."""
    if not path and (agent := os.environ.get(AGENT_ENV, "").strip()):
        return agent
    return local_token(path)


def local_token(path: str | Path | None = None) -> str:
    """Local token shared by the two processes of one machine, never shown in an interface."""
    p = Path(path or os.environ.get("SADA_INTERNAL_TOKEN_FILE") or paths.INTERNAL_TOKEN)
    try:
        t = p.read_text().strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # atomic: two processes may race
    except FileExistsError:
        return p.read_text().strip()
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32))
    return p.read_text().strip()


def check(given: str | None, path: str | Path | None = None) -> bool:
    """Is the token presented by the caller the local one? (constant-time comparison)"""
    return bool(given) and hmac.compare_digest(str(given), local_token(path))


def _digest(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def new_agent_token(conn, mosque_id: int) -> str:
    """New agent token for a mosque, replacing the previous one. Only its hash is kept:
    the value is shown once, to be given to the mosque's orchestrator."""
    t = "sada_" + secrets.token_urlsafe(32)
    conn.execute("UPDATE mosques SET agent_token_hash = ? WHERE id = ?", (_digest(t), mosque_id))
    return t


def revoke_agent_token(conn, mosque_id: int) -> None:
    conn.execute("UPDATE mosques SET agent_token_hash = '' WHERE id = ?", (mosque_id,))


def agent_mosque(conn, given: str | None) -> int | None:
    """Mosque whose agent token is `given`, or None (lookup by hash: nothing to compare in clear)."""
    if not given:
        return None
    row = conn.execute("SELECT id FROM mosques WHERE agent_token_hash = ?", (_digest(str(given)),)).fetchone()
    return row[0] if row else None


def new_publish_key(conn, mosque_id: int) -> str:
    """New stream key for the mosque's OBS (replaces the previous one); only its hash is kept."""
    k = secrets.token_urlsafe(24)
    conn.execute("UPDATE mosques SET publish_key_hash = ? WHERE id = ?", (_digest(k), mosque_id))
    return k


def publish_allowed(conn, slug: str, key: str | None) -> bool:
    """Whether `key` is the stream key of the mosque `slug` (no key set = nobody may publish)."""
    if not slug or not key:
        return False
    row = conn.execute("SELECT publish_key_hash FROM mosques WHERE slug = ?", (slug,)).fetchone()
    return bool(row and row[0]) and hmac.compare_digest(row[0], _digest(str(key)))
