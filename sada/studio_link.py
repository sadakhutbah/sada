"""Sada Studio live, seen by the orchestrator.

A live ordered from the Studio application (manual, or a scheduled slot) reaches the relay like an OBS
stream; the orchestrator then does exactly what it does for OBS: relay to the destinations, record, cut the
replay into sessions for the pipeline. This module adds the two things OBS cannot say:

* **what is on air**: a manual live is not in the plan, so the orchestrator opens an off-programme session
  (type and title chosen on the device) and ends it when the device stops it (or when the server says the
  live is over: silent device, revocation, takeover); a scheduled slot is already in the plan, only the type
  and title announced on the device are attached to it;
* **nothing heavy during a live**: the back office is asked every few seconds from a small background
  thread (one tiny JSON); the machine only receives the last answer, under its own lock.

The back office being unreachable never ends a live (no answer = no change).
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from .i18n import _

log = logging.getLogger("sada")

POLL_S = 5.0
TYPES = ("khutba", "dars", "conference")


@dataclass(frozen=True)
class Announcement:
    """What the back office says about the studio live: {live, source, type, title, slot_id}."""

    live: bool = False
    source: str | None = None  # "manual" | "schedule"
    type: str | None = None
    title: str = ""
    slot_id: str | None = None

    @classmethod
    def parse(cls, data: dict) -> Announcement:
        kind = str(data.get("type") or "").strip().lower()
        return cls(
            live=bool(data.get("live")),
            source=data.get("source") if data.get("source") in ("manual", "schedule") else None,
            type=kind if kind in TYPES else None,
            title=" ".join(str(data.get("title") or "").split())[:200],
            slot_id=data.get("slot_id"),
        )


def _http_get(url: str, token: str) -> dict:  # pragma: no cover (network)
    from .backoffice.internal import HEADER

    req = urllib.request.Request(url, headers={HEADER: token})
    with urllib.request.urlopen(req, timeout=3) as r:  # noqa: S310 — configured back office
        return json.loads(r.read(65536))


def fetch(backoffice_url: str, api_port: int, getter: Callable[[str, str], dict] | None = None) -> Announcement | None:
    """The current announcement, or None when the back office does not answer."""
    if not backoffice_url:
        return None
    from .backoffice.internal import token

    url = backoffice_url.rstrip("/") + "/api/v1/internal/studio-live?" + urlencode({"port": api_port or 0})
    try:
        return Announcement.parse((getter or _http_get)(url, token()) or {})
    except Exception as e:  # noqa: BLE001 — never a reason to touch a live
        log.debug("studio-live unreachable: %s", e)
        return None


class Follower:
    """Aligns the machine on the studio announcement. `step` is cheap and pure of any I/O."""

    def __init__(self):
        self.owned = False  # the machine's current session was opened by the studio
        self.slot_on = False  # a scheduled live was ordered at the last look

    def step(self, machine, sig, ann: Announcement | None) -> str | None:
        """One decision. Returns a log line when something changed (None otherwise)."""
        if ann is None:
            return None  # silence of the back office: nothing changes
        if ann.live and ann.source == "manual":
            return self._manual_on(machine, sig, ann)
        if ann.live and ann.source == "schedule":
            self.slot_on = True
            return self._slot_on(machine, ann)
        slot_over, self.slot_on = self.slot_on, False
        if slot_over and not machine.override and machine.live is not None and not machine.adhoc_on_air:
            return _("Studio slot over: {msg}", msg=machine.end_planned(sig, "fin du créneau Studio"))
        return self._off(machine, sig)

    def _manual_on(self, machine, sig, ann: Announcement) -> str | None:
        kind = ann.type or "khutba"
        if machine.adhoc_on_air:
            r = machine.live
            if (r.kind, r.title) != (kind, ann.title):
                r.kind, r.title = kind, ann.title
                return _("Studio live: announcement updated ({kind}, “{title}”)", kind=kind, title=ann.title)
            return None
        if not machine.can_start_adhoc():
            return None  # a planned session is on air: the studio does not take it over
        msg = machine.start_adhoc(sig, kind, ann.title)
        self.owned = True
        return _("Studio live: {msg} (“{title}”)", msg=msg, title=ann.title)

    def _slot_on(self, machine, ann: Announcement) -> str | None:
        """A scheduled slot: the machine plays it from the plan; the announced type and title name the replay."""
        r = machine.live
        if r is None or r.status != "live" or r.planned.startswith("studio@") or not (ann.type or ann.title):
            return None
        kind = ann.type or r.kind
        if (r.kind, r.title) == (kind, ann.title or r.title):
            return None
        r.kind, r.title = kind, ann.title or r.title
        return _("Studio live: announcement attached to the session ({kind}, “{title}”)", kind=kind, title=r.title)

    def _off(self, machine, sig) -> str | None:
        if self.owned and machine.adhoc_on_air:
            self.owned = False
            return _("Studio live over: {msg}", msg=machine.end_adhoc(sig))
        if not machine.adhoc_on_air:
            self.owned = False
        return None


class Poller:
    """Asks the back office every `POLL_S` seconds in a daemon thread and keeps the last answer."""

    def __init__(self, backoffice_url: str, api_port: int, getter=None, every: float = POLL_S):
        self.url, self.port, self.getter, self.every = backoffice_url, api_port, getter, every
        self.last: Announcement | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.url or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="sada-studio-link", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def take(self) -> Announcement | None:
        """The last answer (None before the first one, or while the back office is silent)."""
        return self.last

    def _run(self) -> None:
        while not self._stop.is_set():
            self.last = fetch(self.url, self.port, self.getter)
            self._stop.wait(self.every)
