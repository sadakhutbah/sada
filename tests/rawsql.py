"""Raw SQL probes of the tests.

The back office queries through SQLAlchemy only; the tests still need to look at the tables
themselves (`PRAGMA table_info`, `sqlite_master`, a stale column, a row the API does not expose).
These helpers run that SQL on the session's own connection, and commit right away: one statement
per transaction, like the autocommit connection they replaced. Leaving a read transaction open
would keep a stale snapshot and the next write of the same session would fail ("database is
locked") as soon as another connection had written in between.

`?` placeholders keep the probes readable: they are switched to `%s` when the suite runs on
PostgreSQL. Everything SQLite-specific (PRAGMA, sqlite_master) stays in tests that are skipped
there.
"""

from __future__ import annotations


class Rows:
    """Rows of one probe, already read: its transaction is closed before the caller uses them."""

    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, i):
        return self._rows[i]

    def __len__(self):
        return len(self._rows)


def _params(args) -> tuple | None:
    if len(args) == 1 and isinstance(args[0], (tuple, list)):
        return tuple(args[0])
    return tuple(args) or None


def run(conn, sql: str, *args) -> Rows:
    """Driver-level rows: read by name (`r._mapping["id"]`) or by position (`r[0]`)."""
    if conn.get_bind().dialect.name == "postgresql":
        sql = sql.replace("?", "%s")
    res = conn.connection().exec_driver_sql(sql, _params(args))
    rows = res.fetchall() if res.returns_rows else []
    conn.commit()
    return Rows(rows)


def one(conn, sql: str, *args) -> dict | None:
    rows = run(conn, sql, *args)
    return dict(rows[0]._mapping) if len(rows) else None


def all_(conn, sql: str, *args) -> list[dict]:
    return [dict(r._mapping) for r in run(conn, sql, *args)]
