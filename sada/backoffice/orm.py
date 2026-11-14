"""SQLAlchemy ORM session of the back office (SQLite file or PostgreSQL).

`Db` is a `sqlalchemy.orm.Session`; every query goes through SQLAlchemy (`select`, `update`,
`insert`, mapped classes of `models`), never through SQL strings.

Transactions: each write helper commits at once (`done`), unless a `transaction()` is open, which
commits its statements together; the request dependency and `close()` commit what is left.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

ENV = "SADA_DATABASE_URL"


def database_url() -> str:
    """PostgreSQL URL from `SADA_DATABASE_URL`; empty = SQLite files."""
    u = os.environ.get(ENV, "").strip()
    return u if u.startswith(("postgres://", "postgresql://")) else ""


def schema_for(path, default) -> str:
    """PostgreSQL schema of the logical database `path`: `public` for the default one, an isolated
    schema per other path (each test gets its own)."""
    if str(path) == str(default):
        return "public"
    return "t_" + hashlib.sha1(str(path).encode()).hexdigest()[:16]


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
    url = database_url()
    key = (url, schema_for(path, default_path)) if url else ("sqlite", str(Path(path)))
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


class Db(Session):
    """Session of one request or job. `_explicit` marks an open `transaction()`: statements inside
    it are committed together at its end instead of one by one (`done`)."""

    def __init__(self, eng: sa.Engine):
        super().__init__(bind=eng, expire_on_commit=False, autoflush=True)
        self._explicit = False

    # Reads outside a `transaction()` see the database as it is now: the rows are buffered and the
    # read transaction ends at once (with the identity map refreshed), as the former autocommit
    # connection did. A long-lived session (a background job, a test) would otherwise keep a stale
    # snapshot, and its next write would hit "database is locked" on SQLite.
    def execute(self, statement, params=None, *args, **kw):  # type: ignore[override]
        if self._explicit or not isinstance(statement, sa.Select):
            return super().execute(statement, params, *args, **kw)
        stmt = statement.execution_options(populate_existing=True)
        frozen = super().execute(stmt, params, *args, **kw).freeze()
        self.commit()
        return frozen()

    def scalar(self, statement, params=None, *args, **kw):  # type: ignore[override]
        return self.execute(statement, params, *args, **kw).scalar()

    def scalars(self, statement, params=None, *args, **kw):  # type: ignore[override]
        return self.execute(statement, params, *args, **kw).scalars()

    def close(self):  # type: ignore[override]
        try:
            if self.new or self.dirty or self.deleted:
                self.commit()
        finally:
            super().close()


def done(conn) -> None:
    """Commit the work of one statement, unless an explicit transaction is open (`begin`)."""
    if not getattr(conn, "_explicit", False):
        conn.commit()


@contextmanager
def transaction(conn):
    """One transaction around several statements: committed together, or not at all.

    Nested inside an open one, it joins it (the outer one commits)."""
    if getattr(conn, "_explicit", False):
        yield
        return
    conn.commit()
    conn._explicit = True
    try:
        yield
        conn._explicit = False
        conn.commit()
    except BaseException:
        conn._explicit = False
        conn.rollback()
        raise


def upsert(conn, model, keys: tuple[str, ...], **vals) -> None:
    """Insert a row of `model`, or update the one with the same `keys` columns."""
    ins = (pg_insert if conn.get_bind().dialect.name == "postgresql" else sqlite_insert)(model).values(**vals)
    rest = {c: ins.excluded[c] for c in vals if c not in keys}
    conn.execute(
        ins.on_conflict_do_update(index_elements=list(keys), set_=rest) if rest else ins.on_conflict_do_nothing()
    )
    done(conn)


def first(conn, model, *where, **by) -> dict | None:
    """First row of `model` matching `where` / `by` (column = value), as a dict; None if absent."""
    stmt = sa.select(model).where(*where).filter_by(**by).limit(1)
    return as_dict(conn.scalar(stmt.execution_options(populate_existing=True)))


def rows(conn, stmt) -> list[dict]:
    """Rows of a select as dicts: mapped objects (`select(Model)`) or plain columns."""
    d = stmt.column_descriptions
    if len(d) == 1 and d[0].get("entity") is not None and d[0].get("type") is d[0]["entity"]:
        return [as_dict(o) for o in conn.scalars(stmt.execution_options(populate_existing=True))]
    return [dict(r._mapping) for r in conn.execute(stmt)]


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
