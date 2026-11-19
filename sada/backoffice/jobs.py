"""Background jobs: one status vocabulary and one small lifecycle shared by the back-office jobs.

`JobStatus` values are the French strings already stored in the database / JSON state files and read
by the front end: they must never change.

`Job` is a template for "start one thing in a daemon thread and show its state":

* `start` refuses a second run (state already running, or already active in this process), records
  the running state, then runs the job-specific target in a daemon thread (or inline for tests);
* `read` returns the stored state with interruption recovery (a running state whose owner is gone
  is reported as failed instead of running forever);
* `mark_ready` / `mark_failed` record the end of a run.

Where the state lives is pluggable: `JsonFileStore` (one JSON file, owner = process id) or
`DbRowStore` (one table row, interrupted runs are recovered once at boot by `recover_interrupted`).
"""

from __future__ import annotations

import enum
import json
import os
import threading
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Any

from sqlalchemy import update

from .. import telemetry
from ..i18n import _
from . import db, orm


class JobStatus(str, enum.Enum):
    """Stored status values (French on purpose: persisted and consumed by the front end)."""

    PENDING = "attente"  # queued, not started
    RUNNING = "en_cours"
    READY = "pret"  # finished successfully (habillage, translation)
    FAILED = "echec"
    DONE = "termine"  # finished successfully (post-live processing)
    ERROR = "erreur"  # failure value of a clip regeneration (regen_state)

    def __str__(self) -> str:
        return self.value


# --------------------------------------------------------------------------- storage


class JobStore:
    """Where a job's state lives. `ref` is the entity dict the job is about, `conn` a DB connection."""

    def read(self, conn, ref: dict) -> dict:
        raise NotImplementedError

    def write(self, conn, ref: dict, state: JobStatus | None, error: str | None, extra: dict) -> None:
        """Set the status and/or error (when not None) and the raw `extra` fields."""
        raise NotImplementedError

    def is_orphan(self, row: dict) -> bool:
        """A running state whose owner has disappeared."""
        return False

    def recover_interrupted(self, conn) -> None:
        """Mark every run left running by a stopped server as failed (no-op for per-read recovery)."""


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


class JsonFileStore(JobStore):
    """State in one JSON file written atomically; the owner is the process that started the run."""

    def __init__(self, path_of: Callable[[dict], Path]):
        self.path_of = path_of

    def read(self, conn, ref: dict) -> dict:
        try:
            data = json.loads(self.path_of(ref).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, conn, ref: dict, state: JobStatus | None, error: str | None, extra: dict) -> None:
        fields = dict(extra)
        if state is not None:
            fields["status"] = state.value
            if state is JobStatus.RUNNING:
                fields["pid"] = os.getpid()
        if error is not None:
            fields["error"] = error
        data = self.read(conn, ref)
        data.update(fields)
        p = self.path_of(ref)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass

    def is_orphan(self, row: dict) -> bool:
        return not pid_alive(row.get("pid"))


class DbRowStore(JobStore):
    """State in a table row (`key_column` matches `ref["id"]`), recovered at boot.

    `ready` / `failed` are the values stored for those two outcomes (a clip stores "" and "erreur").
    """

    def __init__(
        self,
        model,
        key_column: str,
        status_column: str,
        error_column: str,
        *,
        ready: JobStatus | str = JobStatus.READY,
        failed: JobStatus | str = JobStatus.FAILED,
    ):
        self.model, self.key_column = model, key_column
        self.status_column, self.error_column = status_column, error_column
        self.ready, self.failed = ready, failed

    def _where(self, ref: dict):
        return getattr(self.model, self.key_column) == ref["id"]

    def _encode(self, state: JobStatus) -> str:
        if state is JobStatus.READY:
            return str(self.ready)
        if state is JobStatus.FAILED:
            return str(self.failed)
        return state.value

    def read(self, conn, ref: dict) -> dict:
        row = orm.first(conn, self.model, **{self.key_column: ref["id"]})
        if not row:
            return {}
        # Same keys as every store, whatever the column names.
        return {**row, "status": row[self.status_column], "error": row[self.error_column]}

    def ensure_row(self, conn, ref: dict) -> None:
        """Hook for tables whose row may not exist yet when a run starts."""

    def write(self, conn, ref: dict, state: JobStatus | None, error: str | None, extra: dict) -> None:
        fields = dict(extra)
        if state is not None:
            fields[self.status_column] = self._encode(state)
        if error is not None:
            fields[self.error_column] = error
        fields.setdefault("updated_at", db.now_iso())
        self.ensure_row(conn, ref)
        conn.execute(update(self.model).where(self._where(ref)).values(**fields))
        orm.done(conn)

    def recover_interrupted(self, conn) -> None:
        conn.execute(
            update(self.model)
            .where(getattr(self.model, self.status_column) == JobStatus.RUNNING.value)
            .values(
                **{
                    self.status_column: self._encode(JobStatus.FAILED),
                    self.error_column: _("interrupted (server stopped)"),
                    "updated_at": db.now_iso(),
                }
            )
        )
        orm.done(conn)


# --------------------------------------------------------------------------- job


class Job:
    """Lifecycle of one kind of background job. Subclass or instantiate with a store."""

    def __init__(
        self,
        store: JobStore,
        *,
        busy_message: Callable[[], str],
        thread_name: str = "job",
        key: Callable[[dict], Hashable] = lambda ref: ref["id"],
    ):
        self.store, self.busy_message, self.thread_name, self._key = store, busy_message, thread_name, key
        self._lock = threading.Lock()
        self._active: set[Hashable] = set()  # one run per entity in this process
        self._outcomes: dict[Hashable, str] = {}  # how each running job ended (`mark_ready` / `mark_failed`)

    # -- state

    def read(self, conn, ref: dict) -> dict:
        """Stored state; a run whose owner is gone is reported as failed (not persisted)."""
        row = self.store.read(conn, ref)
        if row.get("status") == JobStatus.RUNNING.value and self.store.is_orphan(row):
            row = {**row, "status": JobStatus.FAILED.value, "error": _("interrupted (server stopped)")}
        return row

    def is_running(self, conn, ref: dict) -> bool:
        return self.read(conn, ref).get("status") == JobStatus.RUNNING.value

    def ensure_idle(self, conn, ref: dict) -> None:
        if self.is_running(conn, ref):
            raise ValueError(self.busy_message())

    def recover_interrupted(self, conn) -> None:
        self.store.recover_interrupted(conn)

    # -- lifecycle

    def start(
        self,
        conn,
        ref: dict,
        target: Callable[..., None],
        args: tuple = (),
        *,
        fields: dict | None = None,
        before_spawn: Callable[[], None] | None = None,
        background: bool = True,
    ) -> None:
        """Refuse a double start, record the running state, then run `target(*args)`.

        `target` owns its own connection and ends the run with `mark_ready` / `mark_failed`.
        `before_spawn` runs once the running state is recorded (audit, bookkeeping).
        """
        self.ensure_idle(conn, ref)
        key = self._key(ref)
        with self._lock:
            if key in self._active:
                raise ValueError(self.busy_message())
            self._active.add(key)
        try:
            self.store.write(conn, ref, JobStatus.RUNNING, None, fields or {})
            if before_spawn:
                before_spawn()
        except BaseException:
            self._release(key)
            raise
        if background:
            threading.Thread(
                target=self._guarded, args=(key, target, args), daemon=True, name=f"{self.thread_name}-{key}"
            ).start()
        else:
            self._guarded(key, target, args)

    def _guarded(self, key: Hashable, target: Callable[..., None], args: tuple) -> None:
        try:
            with telemetry.get().track("job", kind=self.thread_name) as run:
                target(*args)
                if self._outcomes.get(key) == telemetry.ERROR:
                    run.fail()  # the job recorded its own failure instead of raising
        finally:
            self._outcomes.pop(key, None)
            self._release(key)

    def _release(self, key: Hashable) -> None:
        with self._lock:
            self._active.discard(key)

    def mark_ready(self, conn, ref: dict, **extra: Any) -> None:
        self._outcomes[self._key(ref)] = telemetry.OK
        self.store.write(conn, ref, JobStatus.READY, "", extra)

    def mark_failed(self, conn, ref: dict, error: str, **extra: Any) -> None:
        self._outcomes[self._key(ref)] = telemetry.ERROR
        self.store.write(conn, ref, JobStatus.FAILED, error, extra)

    def update(self, conn, ref: dict, **extra: Any) -> None:
        """Record progress (or any other field) without touching the status."""
        self.store.write(conn, ref, None, None, extra)
