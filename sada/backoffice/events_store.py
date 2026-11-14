"""Programme of dars and conferences, stored per mosque in the database.

Entries keep the shape of the former `events.json` (raw dicts validated by `sada.events`), so the
engine reads them unchanged; only where they live changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import events as events_mod


def read(conn, mosque_id: int) -> list[dict]:
    rows = conn.execute("SELECT data FROM events WHERE mosque_id = ? ORDER BY position, id", (mosque_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def write(conn, mosque_id: int, items: list[dict]) -> None:
    """Replace the mosque's programme (one transaction: never half written)."""
    rows = [
        (mosque_id, events_mod.uid_of(raw, i), json.dumps(raw, ensure_ascii=False), i) for i, raw in enumerate(items)
    ]
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM events WHERE mosque_id = ?", (mosque_id,))
        conn.executemany("INSERT INTO events (mosque_id, uid, data, position) VALUES (?, ?, ?, ?)", rows)
    except Exception:
        if own:
            conn.execute("ROLLBACK")
        raise
    if own:
        conn.execute("COMMIT")


def adopt(conn, mosque_id: int, path: str | Path) -> bool:
    """One-time import of the former programme file, while the instance has a single mosque
    (the file had no notion of mosque: with several, nobody can say whose it is).

    The file is renamed `.imported` afterwards. An unreadable file is left alone (ValueError).
    """
    p = Path(path)
    if not p.is_file():
        return False
    if conn.execute("SELECT COUNT(*) FROM mosques").fetchone()[0] != 1:
        return False
    if conn.execute("SELECT 1 FROM events WHERE mosque_id = ? LIMIT 1", (mosque_id,)).fetchone():
        return False
    items = events_mod.read_raw(p)
    write(conn, mosque_id, items)
    p.rename(p.with_name(p.name + ".imported"))
    return True
