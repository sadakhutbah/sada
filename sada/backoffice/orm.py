"""SQLAlchemy ORM session of the back office (SQLite file or PostgreSQL).

`Db` is a `sqlalchemy.orm.Session`: migrated code uses it as such (`db.scalars(select(User)...)`,
`db.add(...)`, `db.get(Mosque, id)`). It also still accepts the former raw SQL calls
(`conn.execute("... ?", args)`) in the same transaction, until every module is migrated.

Transactions: a raw write is committed at once unless an explicit `BEGIN` is open (former
autocommit behaviour); ORM work is committed by `commit()`, done by the request dependency at the
end of each request and by `close()`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import Session

from . import pg

# One engine per database (URL + schema), kept for the life of the process: every request checks a
# connection out of its pool instead of opening one. A request that opened a fresh connection paid
# for the handshake, and on PostgreSQL for `CREATE SCHEMA` + `SET search_path` as well.
POOL_SIZE = 5
MAX_OVERFLOW = 10
POOL_TIMEOUT = 10  # seconds to wait for a free connection before failing (default is 30)
ENGINES_KEPT = 16  # LRU: the tests open a throwaway database per test, production has one or two

_ENGINES: OrderedDict[tuple, sa.Engine] = OrderedDict()
_LOCK = threading.Lock()


def engine(path: str | Path, default_path) -> sa.Engine:
    """Shared engine of the database `path` (its own schema when PostgreSQL is configured)."""
    url = pg.url()
    key = (url, pg.schema_for(path, default_path)) if url else ("sqlite", str(Path(path)))
    with _LOCK:
        eng = _ENGINES.get(key)
        if eng is not None:
            _ENGINES.move_to_end(key)
            return eng
        while len(_ENGINES) >= ENGINES_KEPT:
            _, old = _ENGINES.popitem(last=False)
            old.dispose()  # idle connections closed; a checked-out one is dropped when returned
        eng = _ENGINES[key] = _make(key, path)
    return eng


def _make(key, path) -> sa.Engine:
    if key[0] == "sqlite":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        eng = sa.create_engine(
            f"sqlite:///{Path(path)}",
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT,
            connect_args={"timeout": 10, "check_same_thread": False, "isolation_level": None},
        )

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi, _rec):
            for p in ("foreign_keys = ON", "journal_mode = WAL", "busy_timeout = 10000"):
                dbapi.execute(f"PRAGMA {p}")

        @event.listens_for(eng, "begin")
        def _begin(conn):  # pysqlite autocommit: SQLAlchemy must emit BEGIN itself
            conn.exec_driver_sql("BEGIN")

        return eng
    url, schema = key
    eng = sa.create_engine(
        url.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1),
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        pool_pre_ping=True,  # a connection dropped by the server (restart, idle timeout) is replaced
    )

    @event.listens_for(eng, "connect")
    def _schema(dbapi, _rec):
        with dbapi.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
        dbapi.commit()

    return eng


class _Result:
    """Raw-SQL result with the sqlite3 cursor surface (`fetchone`, `fetchall`, `lastrowid`)."""

    def __init__(self, res, lastrowid=None):
        self.lastrowid = lastrowid
        self.rowcount = getattr(res, "rowcount", -1)
        self._rows = None
        if res is not None and res.returns_rows and lastrowid is None:
            names = list(res.keys())
            self._rows = [pg.Row(names, tuple(r)) for r in res.fetchall()]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows or [])

    def __iter__(self):
        return iter(self.fetchall())


class Db(Session):
    def __init__(self, eng: sa.Engine):
        super().__init__(bind=eng, expire_on_commit=False, autoflush=True)
        self._explicit = False
        self._pg = eng.dialect.name == "postgresql"

    # ---------------------------------------------------------------- former raw SQL
    @property
    def in_transaction(self) -> bool:  # type: ignore[override]
        return self._explicit

    def execute(self, statement, params=None, *args, **kw):  # type: ignore[override]
        if not isinstance(statement, str):
            return super().execute(statement, params, *args, **kw)
        return self._raw(statement, params or ())

    def executemany(self, sql: str, seq):
        for p in seq:
            self._raw(sql, p)

    def _raw(self, sql: str, params):
        head = sql.lstrip()[:20].upper()
        if head.startswith("BEGIN"):
            self._explicit = True
            return _Result(None)
        if head.startswith("COMMIT"):
            self._explicit = False
            self.commit()
            return _Result(None)
        if head.startswith("ROLLBACK"):
            self._explicit = False
            self.rollback()
            return _Result(None)
        if self._pg:
            if head.startswith("PRAGMA"):
                return _Result(None)
            sql = pg.rewrite_catalog(sql)
            sql, returning = pg.translate(sql)
        else:
            returning = False
        self.flush()
        conn = self.connection()
        res = conn.exec_driver_sql(sql, tuple(pg._value(v) for v in params))
        lastrowid = None
        if returning:
            r = res.fetchone()
            lastrowid = r[0] if r else None
        elif not self._pg and head.startswith("INSERT"):
            lastrowid = res.lastrowid
        out = _Result(res, lastrowid)
        if not self._explicit:  # former autocommit: also ends the read snapshot
            self.commit()
        return out

    def close(self):  # type: ignore[override]
        try:
            if self.new or self.dirty or self.deleted:
                self.commit()
        finally:
            super().close()


def as_dict(obj) -> dict | None:
    """Mapped object -> plain dict of its columns (what the API layer hands around)."""
    if obj is None:
        return None
    return {c.key: getattr(obj, c.key) for c in sa.inspect(obj).mapper.column_attrs}


def row(conn, model, ident: int) -> dict | None:
    """One row read from the database, not from the identity map.

    A session keeps the objects it loaded; after an `UPDATE` they still hold the former values, so a
    module that needs the row back reads it again with `populate_existing`."""
    stmt = sa.select(model).where(model.id == ident).execution_options(populate_existing=True)
    return as_dict(conn.scalar(stmt))
