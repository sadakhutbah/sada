"""PostgreSQL behind the same small surface the back office uses on sqlite3 connections.

Selected by `SADA_DATABASE_URL` (postgresql://...). The application keeps writing its SQL with `?`
placeholders and reading rows by name or by index; this module translates the few SQLite-isms
(`?`, `INSERT OR IGNORE`, `LIKE`, `PRAGMA`, `sqlite_master`, `lastrowid`) so that one code path
serves both databases. Each logical database (file path) maps to its own schema, `public` for the
default one: tests get an isolated schema each, production uses `public`.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading

ENV = "SADA_DATABASE_URL"


def url() -> str:
    u = os.environ.get(ENV, "").strip()
    return u if u.startswith(("postgres://", "postgresql://")) else ""


def schema_for(path, default) -> str:
    if str(path) == str(default):
        return "public"
    return "t_" + hashlib.sha1(str(path).encode()).hexdigest()[:16]


_INSERT_TABLE = re.compile(r"^\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)", re.I)
_OR_IGNORE = re.compile(r"^(\s*INSERT)\s+OR\s+IGNORE\b", re.I)


def _tables_with_id() -> frozenset[str]:
    from . import models

    return frozenset(t.name for t in models.metadata.sorted_tables if "id" in t.c and t.c["id"].primary_key)


def translate(sql: str) -> tuple[str, bool]:
    """(PostgreSQL SQL, whether it returns the new id)."""
    s = sql
    ignore = bool(_OR_IGNORE.match(s))
    if ignore:
        s = _OR_IGNORE.sub(r"\1", s) + " ON CONFLICT DO NOTHING"
    # users.username is COLLATE NOCASE on SQLite
    s = re.sub(r"\busername\s*=\s*\?", "lower(username) = lower(?)", s)
    s = re.sub(r"(?<![\w\"])end(?![\w\"])", '"end"', s)  # clips.end (lowercase: never CASE ... END)
    s = re.sub(r"\bIS\s+NOT\s+\?", "IS DISTINCT FROM ?", s)
    s = re.sub(r"datetime\('now'\)", "to_char(now() at time zone 'utc', 'YYYY-MM-DD HH24:MI:SS')", s)
    s = re.sub(r"\bLIKE\b", "ILIKE", s)  # SQLite LIKE ignores ASCII case
    s = s.replace("%", "%%").replace("?", "%s")
    s = re.sub(r"\bBEGIN\s+IMMEDIATE\b", "BEGIN", s, flags=re.I)
    m = _INSERT_TABLE.match(sql)
    returning = bool(m) and m.group(1) in _tables_with_id() and "RETURNING" not in s.upper()
    if returning:
        s += " RETURNING id"
    return s, returning


def rewrite_catalog(sql: str) -> str:
    if "SQLITE_MASTER" not in sql.upper():
        return sql
    return re.sub(
        r"SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*\?",
        "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
        sql,
        flags=re.I,
    )


class Row(dict):
    """sqlite3.Row lookalike: by name, by position, `keys()`, `dict(row)`."""

    __slots__ = ("_vals",)

    def __init__(self, names, values):
        super().__init__(zip(names, values, strict=False))
        self._vals = tuple(values)

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        return super().__getitem__(k)

    def __iter__(self):
        return iter(self._vals)


def _value(v):
    return int(v) if isinstance(v, bool) else v


class Cursor:
    def __init__(self, cur, returning: bool):
        self._cur = cur
        self.lastrowid = None
        self.rowcount = cur.rowcount
        self._pending = None
        if returning and cur.description:
            r = cur.fetchone()
            self.lastrowid = r[0] if r else None

    def _rows(self, rows):
        if not self._cur.description:
            return []
        names = [d.name for d in self._cur.description]
        return [Row(names, r) for r in rows]

    def fetchone(self):
        if self.lastrowid is not None or not self._cur.description:
            return None
        r = self._cur.fetchone()
        return self._rows([r])[0] if r else None

    def fetchall(self):
        if self.lastrowid is not None or not self._cur.description:
            return []
        return self._rows(self._cur.fetchall())

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    """Autocommit by default (like `isolation_level=None`), explicit BEGIN/COMMIT honoured."""

    def __init__(self, dsn: str, schema: str):
        import psycopg

        self._c = psycopg.connect(dsn, autocommit=True)
        self._lock = threading.RLock()
        self.schema = schema
        self.row_factory = None
        with self._c.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')

    @property
    def in_transaction(self) -> bool:
        import psycopg.pq

        return self._c.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def execute(self, sql: str, params=()):
        head = sql.lstrip()[:16].upper()
        if head.startswith("PRAGMA"):
            return _Empty()
        sql = rewrite_catalog(sql)
        s, returning = translate(sql)
        with self._lock:
            cur = self._c.cursor()
            cur.execute(s, tuple(_value(v) for v in (params or ())))
            return Cursor(cur, returning)

    def executemany(self, sql: str, seq):
        for p in seq:
            self.execute(sql, p)

    def commit(self):
        if self.in_transaction:
            self._c.execute("COMMIT")

    def rollback(self):
        if self.in_transaction:
            self._c.execute("ROLLBACK")

    def close(self):
        self._c.close()


class _Empty:
    lastrowid = None
    rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())
