"""Programme of dars and conferences, stored per mosque in the database.

Entries keep the shape of the former `events.json` (raw dicts validated by `sada.events`), so the
engine reads them unchanged; only where they live changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, func, insert, select

from .. import events as events_mod
from . import orm
from .models import Event, Mosque


def read(conn, mosque_id: int) -> list[dict]:
    stmt = select(Event.data).where(Event.mosque_id == mosque_id).order_by(Event.position, Event.id)
    return [json.loads(d) for d in conn.scalars(stmt)]


def write(conn, mosque_id: int, items: list[dict]) -> None:
    """Replace the mosque's programme (one transaction: never half written)."""
    rows = [
        {
            "mosque_id": mosque_id,
            "uid": events_mod.uid_of(raw, i),
            "data": json.dumps(raw, ensure_ascii=False),
            "position": i,
        }
        for i, raw in enumerate(items)
    ]
    with orm.transaction(conn):
        conn.execute(delete(Event).where(Event.mosque_id == mosque_id))
        if rows:
            conn.execute(insert(Event), rows)


def adopt(conn, mosque_id: int, path: str | Path) -> bool:
    """One-time import of the former programme file, while the instance has a single mosque
    (the file had no notion of mosque: with several, nobody can say whose it is).

    The file is renamed `.imported` afterwards. An unreadable file is left alone (ValueError).
    """
    p = Path(path)
    if not p.is_file():
        return False
    if conn.scalar(select(func.count()).select_from(Mosque)) != 1:
        return False
    if conn.scalar(select(Event.id).where(Event.mosque_id == mosque_id).limit(1)) is not None:
        return False
    items = events_mod.read_raw(p)
    write(conn, mosque_id, items)
    p.rename(p.with_name(p.name + ".imported"))
    return True
