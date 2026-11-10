"""SQLite database of the back-office (stdlib sqlite3, no ORM).

Every business table carries `mosque_id`: that is the partitioning key (tenant). Queries from the
application ALWAYS filter by the mosque of the logged-in user.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .. import paths
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB = paths.BACKOFFICE_DB
# settings a mosque can change (Settings → Mosque, `python -m minbar.backoffice mosque`)
MOSQUE_SETTINGS = ("name", "timezone", "mawaqit_url", "orchestrator_url", "orchestrator_token", "state_dir", "output_dir",
                   "publish_dir", "clip_title", "logo_path")
MASK = "••••••"                  # value shown in place of a stored secret; sent back as is = unchanged

SCHEMA = """
CREATE TABLE IF NOT EXISTS mosques (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Europe/Paris',
    mawaqit_url TEXT NOT NULL DEFAULT '',
    orchestrator_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:8088',
    orchestrator_token TEXT NOT NULL DEFAULT '',
    state_dir TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL DEFAULT '',
    publish_dir TEXT NOT NULL DEFAULT '',
    clip_title TEXT NOT NULL DEFAULT 'Khutba du vendredi',
    default_imam_id INTEGER,
    auto_import INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    mosque_id INTEGER NOT NULL REFERENCES mosques(id),
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'responsable', 'imam')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    ip TEXT, user_agent TEXT,
    flash TEXT
);
CREATE TABLE IF NOT EXISTS khutbas (
    id INTEGER PRIMARY KEY,
    mosque_id INTEGER NOT NULL REFERENCES mosques(id),
    run_id TEXT NOT NULL,
    session_index INTEGER NOT NULL,
    label TEXT NOT NULL,
    planned TEXT,
    date TEXT,
    start_clock TEXT, end_clock TEXT,
    duration_s REAL,
    imam_id INTEGER REFERENCES users(id),
    speaker TEXT NOT NULL DEFAULT '',
    theme TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL,
    recording TEXT, start_pos REAL, end_pos REAL,
    video_path TEXT,
    verses_count INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'khutba',
    title TEXT NOT NULL DEFAULT '',
    series TEXT NOT NULL DEFAULT '',
    session_no INTEGER,
    imported_at TEXT NOT NULL,
    UNIQUE (mosque_id, run_id, session_index)
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    mosque_id INTEGER NOT NULL REFERENCES mosques(id),
    khutba_id INTEGER NOT NULL REFERENCES khutbas(id),
    idx INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    why TEXT NOT NULL DEFAULT '',
    start REAL NOT NULL, "end" REAL NOT NULL,
    file_path TEXT NOT NULL,
    video_source TEXT, source_offset REAL, crop TEXT,
    status TEXT NOT NULL DEFAULT 'a_valider' CHECK (status IN ('a_valider', 'valide', 'rejete', 'publie')),
    version INTEGER NOT NULL DEFAULT 1,
    needs_regen INTEGER NOT NULL DEFAULT 0,
    regen_state TEXT NOT NULL DEFAULT '',
    regen_error TEXT NOT NULL DEFAULT '',
    decided_by INTEGER REFERENCES users(id), decided_at TEXT, reject_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (khutba_id, idx)
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    mosque_id INTEGER NOT NULL REFERENCES mosques(id),
    khutba_id INTEGER NOT NULL REFERENCES khutbas(id),
    seg_index INTEGER NOT NULL,
    field TEXT NOT NULL CHECK (field IN ('text', 'fr')),
    original TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE (khutba_id, seg_index, field)
);
CREATE TABLE IF NOT EXISTS connectors (
    mosque_id INTEGER NOT NULL REFERENCES mosques(id),
    key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (mosque_id, key)
);
CREATE TABLE IF NOT EXISTS notifications (
    mosque_id INTEGER PRIMARY KEY REFERENCES mosques(id),
    mode TEXT NOT NULL DEFAULT '' CHECK (mode IN ('', 'webhook', 'telegram')),
    webhook TEXT NOT NULL DEFAULT '',
    telegram_token TEXT NOT NULL DEFAULT '',
    telegram_chat TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    mosque_id INTEGER NOT NULL REFERENCES mosques(id),
    clip_id INTEGER NOT NULL REFERENCES clips(id),
    clip_version INTEGER NOT NULL,
    connector TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('publie', 'refuse', 'erreur')),
    location TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    mosque_id INTEGER REFERENCES mosques(id),
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT '',
    object_id INTEGER,
    details TEXT NOT NULL DEFAULT '{}',
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_mosque ON audit (mosque_id, id);
CREATE INDEX IF NOT EXISTS clips_mosque ON clips (mosque_id, status);
CREATE INDEX IF NOT EXISTS khutbas_mosque ON khutbas (mosque_id, date);
"""
# columns added after the fact: (table, column, declaration) — applied to existing databases
ADDED_COLUMNS = [
    ("khutbas", "kind", "TEXT NOT NULL DEFAULT 'khutba'"),
    ("khutbas", "title", "TEXT NOT NULL DEFAULT ''"),
    ("khutbas", "series", "TEXT NOT NULL DEFAULT ''"),
    ("khutbas", "session_no", "INTEGER"),
    # logo specific to the back-office (favicon + bar): file under data/backoffice/logos/; empty = neutral emblem
    ("mosques", "logo_path", "TEXT NOT NULL DEFAULT ''"),
]


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


def _migrate(conn) -> None:
    """Adds the missing columns (databases created by an earlier version)."""
    for table, col, decl in ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init(path: str | Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


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
    cols = ["slug", "name", "created_at"] + list(fields)
    vals = [slug, name, now_iso()] + list(fields.values())
    cur = conn.execute(f"INSERT INTO mosques ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    mid = cur.lastrowid
    conn.execute("INSERT OR IGNORE INTO connectors (mosque_id, key, enabled, settings) VALUES (?, 'local', 1, '{}')", (mid,))
    audit(conn, mid, None, "mosquee.creee", "mosquee", mid, slug=slug)
    return mid
