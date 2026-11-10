"""SQLite database of the back-office.

The schema is described by SQLAlchemy 2.0 models (`models.py`, metadata + Core) and applied by
versioned migrations (`migrations.py`). The application keeps querying in raw SQL through the
helpers below (`one`, `all_`, `audit`) on plain `sqlite3` connections: the public surface of this
module is unchanged.

Every business table carries `mosque_id`: that is the partitioning key (tenant). Queries from the
application ALWAYS filter by the mosque of the logged-in user.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .. import defaults, paths
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import migrations, models

DEFAULT_DB = paths.BACKOFFICE_DB
# settings a mosque can change (Settings → Mosque, `python -m minbar.backoffice mosque`)
MOSQUE_SETTINGS = ("name", "timezone", "mawaqit_url", "orchestrator_url", "orchestrator_token", "state_dir", "output_dir",
                   "publish_dir", "clip_title", "logo_path")
MASK = "••••••"                  # value shown in place of a stored secret; sent back as is = unchanged

# schéma SQLAlchemy (source de vérité) et migrations versionnées, réexportés pour introspection
metadata = models.metadata
MIGRATIONS = migrations.MIGRATIONS
LATEST_VERSION = migrations.LATEST


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init(path: str | Path) -> None:
    """Crée ou met à jour la base `path` : schéma des modèles SQLAlchemy + migrations versionnées."""
    migrations.init(path)


def one(conn, sql: str, *args) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def all_(conn, sql: str, *args) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def audit(conn, mosque_id, user_id, action: str, object_type: str = "", object_id=None, ip: str | None = None, **details):
    conn.execute("INSERT INTO audit (mosque_id, user_id, action, object_type, object_id, details, ip, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (mosque_id, user_id, action, object_type, object_id, json.dumps(details, ensure_ascii=False), ip, now_iso()))


# ------------------------------------------------------------------ mosques

def mosque_dirs(m: dict) -> dict:
    """Effective folders of a mosque (empty values = folders of the repository, mode A)."""
    return {
        "state": Path(m.get("state_dir") or paths.STATE),
        "output": Path(m.get("output_dir") or paths.OUTPUT),
        "publish": Path(m.get("publish_dir") or local_folder_default(m)),
    }


def local_folder_default(m: dict) -> Path:
    """Default folder of the "Local folder" connection: data/published/<mosque>."""
    return paths.PUBLISHED / m["slug"]


DEFAULT_TZ = "Europe/Paris"


def timezone(mosque: dict) -> ZoneInfo:
    return ZoneInfo(mosque.get("timezone") or DEFAULT_TZ)


def update_mosque(conn, mosque_id: int, **vals) -> None:
    if "timezone" in vals:
        try:
            timezone(vals)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError(f"fuseau horaire inconnu : {vals['timezone']} (ex. Europe/Paris)") from None
    if vals:
        conn.execute(f"UPDATE mosques SET {', '.join(f'{k} = ?' for k in vals)} WHERE id = ?", (*vals.values(), mosque_id))


def create_mosque(conn, slug: str, name: str, **fields) -> int:
    fields.setdefault("clip_title", defaults.CLIP_TITLE)
    cols = ["slug", "name", "created_at"] + list(fields)
    vals = [slug, name, now_iso()] + list(fields.values())
    cur = conn.execute(f"INSERT INTO mosques ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    mid = cur.lastrowid
    conn.execute("INSERT OR IGNORE INTO connectors (mosque_id, key, enabled, settings) VALUES (?, 'local', 1, '{}')", (mid,))
    audit(conn, mid, None, "mosquee.creee", "mosquee", mid, slug=slug)
    return mid
