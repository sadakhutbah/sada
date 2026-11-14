"""Mawaqit times cached per mosque in the database (fallback when the site is down)."""

from __future__ import annotations

import json
import time

from .. import mawaqit


def times(conn, mosque: dict, fetch=None, now=None) -> dict:
    """Times of the mosque: fresh cache, else Mawaqit (stored), else the stale cache."""
    now = time.time() if now is None else now
    row = conn.execute("SELECT mawaqit_cache, mawaqit_cached_at FROM mosques WHERE id = ?", (mosque["id"],)).fetchone()
    cache = json.loads(row[0]) if row and row[0] else None
    age = now - float(row[1] or 0) if cache else 0.0
    data, stored = mawaqit.resolve(mosque.get("mawaqit_url") or "", cache, age, fetch=fetch)
    if stored is not None:
        conn.execute(
            "UPDATE mosques SET mawaqit_cache = ?, mawaqit_cached_at = ? WHERE id = ?",
            (json.dumps(stored, ensure_ascii=False), now, mosque["id"]),
        )
    return data
