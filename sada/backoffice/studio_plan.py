"""Planning of the studio and the order given to the device (standby or live).

The server is the source of truth: a device only asks "what now?" (heartbeat) and obeys.

**Slots.** Sada already knows the mosque's schedule, so nothing is entered twice. Two sources, behind one
small interface (`SlotSource`, Strategy pattern), are merged:

* `JumuaSource`: the Friday khutba(s), at the times of the mosque's prayer settings (same rule as the
  orchestrator: Fridays only);
* `ProgrammeSource`: the dars, conferences and tarawih of the programme (`events_store`), one-off or weekly,
  fixed time or relative to a prayer, exactly as the orchestrator reads them (`sada.events`).

A slot has an absolute start and end (UTC): the local wall-clock time of the mosque is converted with its
time zone (Europe/Paris by default), so a 20:00 dars is 19:00 UTC in winter and 18:00 UTC in summer, a time
that does not exist (the hour skipped in March) is shifted forward and an ambiguous one (October) takes its
first occurrence. The end is the start plus the duration: `max_min` of the entry, else the mosque's default
(`studio_settings`), counted in real elapsed time.

**Order.** `decide` is a pure function (a small state machine over standby / scheduled live / manual live):

* a manual live (started from the device) is live until it is stopped; a scheduled slot never cuts it;
* otherwise, from `start - margin` to `end` of a slot the order is live, unless that occurrence was stopped by
  hand: it then stays standby until the next slot (no automatic restart);
* otherwise standby, announcing the next slot.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

from sqlalchemy import delete, select, update

from .. import events as events_mod
from .. import prayers
from ..i18n import _
from . import db, events_store, orm, prayer_times
from .models import Device, Mosque, StudioRun, StudioSettings

UTC = dt.UTC
DEFAULTS = {"start_margin_s": 120, "offline_alert_min": 10, "khutba_min": 60, "dars_min": 90, "preflight_min": 30}
LIMITS = {
    "start_margin_s": (30, 900),
    "offline_alert_min": (2, 240),
    "khutba_min": (10, 240),
    "dars_min": (10, 360),
    "preflight_min": (5, 180),
}
KINDS = ("khutba", "dars", "conference", "tarawih")
HORIZON_DAYS = 8
ONLINE_S = 90  # a device that spoke less than this ago is online (the heartbeat is every 30 s)
MANUAL_SILENCE_S = 900  # a manual live whose device is silent this long is over


# ------------------------------------------------------------------ settings


def settings(conn, mosque_id: int) -> dict:
    row = orm.first(conn, StudioSettings, mosque_id=mosque_id) or {}
    return {k: int(row.get(k) or d) for k, d in DEFAULTS.items()}


def save_settings(conn, mosque_id: int, values: dict) -> dict:
    clean = {}
    for k, (lo, hi) in LIMITS.items():
        if k in values:
            try:
                v = int(values[k])
            except (TypeError, ValueError) as e:
                raise ValueError(_("{field} must be a whole number", field=k)) from e
            if not lo <= v <= hi:
                raise ValueError(_("{field} must be between {lo} and {hi}", field=k, lo=lo, hi=hi))
            clean[k] = v
    if clean:
        orm.upsert(conn, StudioSettings, ("mosque_id",), mosque_id=mosque_id, **clean)
    return settings(conn, mosque_id)


# ------------------------------------------------------------------ slots


@dataclass(frozen=True)
class Slot:
    uid: str
    source: str  # jumua | programme
    kind: str
    title: str
    starts: dt.datetime  # aware, UTC
    ends: dt.datetime

    @property
    def key(self) -> str:
        """Identifies one occurrence (a weekly dars has one key per week)."""
        return f"{self.uid}@{self.starts.strftime('%Y%m%dT%H%MZ')}"

    def as_dict(self, now: float | None = None) -> dict:
        d = {
            "slot_id": self.key,
            "type": self.kind,
            "title": self.title,
            "starts_at": self.starts.isoformat().replace("+00:00", "Z"),
            "ends_at": self.ends.isoformat().replace("+00:00", "Z"),
        }
        if now is not None:
            d["starts_in_s"] = int(self.starts.timestamp() - now)
        return d


def to_utc(naive: dt.datetime, tz: dt.tzinfo) -> dt.datetime:
    """Wall-clock time of the mosque -> UTC. Gap: shifted forward; overlap: first occurrence (fold=0)."""
    return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)


@dataclass
class SlotContext:
    conn: object
    mosque: dict
    tz: dt.tzinfo
    cfg: dict
    today: dt.date
    _times: dict = field(default_factory=dict)

    def times(self, day: dt.date) -> tuple[dict, list[str]]:
        """(prayer times {name: "HH:MM"}, jumua times) of a day, as far as the mosque's source tells.

        Today comes from the source itself (Mawaqit included). Other days are only known for the
        calculated and typed sources; a Mawaqit mosque keeps its jumua times (they do not move from a week
        to the next) but has no prayer times for a later day, so a slot relative to a prayer is not planned
        before its day."""
        if day in self._times:
            return self._times[day]
        out: tuple[dict, list[str]] = ({}, [])
        try:
            if day == self.today:
                d = prayer_times.times(self.conn, self.mosque)
                out = (prayers.prayer_times(d), list(d.get("jumua_list") or []))
            else:
                st = prayer_times.read(self.conn, self.mosque)
                if st["source"] in ("computed", "manual"):
                    f = prayer_times.day_fields(self.conn, self.mosque, st, day)
                    d = prayer_times.to_data(f, st, self.mosque, st["source"])
                    out = (prayers.prayer_times(d), list(d.get("jumua_list") or []))
                elif st["source"] == "mawaqit":
                    out = ({}, list(self.times(self.today)[1]))
        except prayer_times.NoTimes:
            pass
        except Exception:  # noqa: BLE001 — a source down must not stop the order: no slot rather than a crash
            pass
        self._times[day] = out
        return out


class SlotSource(Protocol):
    def slots_on(self, day: dt.date, ctx: SlotContext) -> list[Slot]: ...


class JumuaSource:
    """The Friday khutba(s): one slot per jumua time of the mosque's settings."""

    def slots_on(self, day: dt.date, ctx: SlotContext) -> list[Slot]:
        if day.weekday() != 4:
            return []
        out = []
        for i, hhmm in enumerate(ctx.times(day)[1]):
            try:
                start = to_utc(dt.datetime.combine(day, events_mod.parse_hhmm(hhmm)), ctx.tz)
            except ValueError:
                continue
            out.append(
                Slot(
                    f"jumua-{i}",
                    "jumua",
                    "khutba",
                    events_mod.KHUTBA_TITLE,
                    start,
                    start + dt.timedelta(minutes=ctx.cfg["khutba_min"]),
                )
            )
        return out


class ProgrammeSource:
    """Dars, conferences and tarawih of the mosque's programme (the same entries the orchestrator plans)."""

    def slots_on(self, day: dt.date, ctx: SlotContext) -> list[Slot]:
        events = []
        for i, raw in enumerate(events_store.read(ctx.conn, ctx.mosque["id"])):
            try:
                events.append(events_mod.parse(raw, i))
            except ValueError:
                continue  # an entry the engine refuses is not planned by anyone
        out = []
        for inst in events_mod.instances_on(events, day, ctx.times(day)[0]):
            minutes = inst.max_min or ctx.cfg["dars_min"]
            start = to_utc(inst.starts, ctx.tz)
            out.append(
                Slot(
                    inst.uid or inst.kind,
                    "programme",
                    inst.kind,
                    inst.title or events_mod.kind_label(inst.kind),
                    start,
                    start + dt.timedelta(minutes=float(minutes)),
                )
            )
        return out


SOURCES: tuple[SlotSource, ...] = (JumuaSource(), ProgrammeSource())


def upcoming(conn, mosque: dict, now: float | None = None, days: int = HORIZON_DAYS) -> list[Slot]:
    """Slots that are running or still to come, soonest first (yesterday's are looked at: one may run past midnight)."""
    now = time.time() if now is None else now
    tz = db.timezone(mosque)
    today = dt.datetime.fromtimestamp(now, UTC).astimezone(tz).date()
    ctx = SlotContext(conn, mosque, tz, settings(conn, mosque["id"]), today)
    found: dict[str, Slot] = {}
    for k in range(-1, days + 1):
        day = today + dt.timedelta(days=k)
        for source in SOURCES:
            for slot in source.slots_on(day, ctx):
                if slot.ends.timestamp() > now:
                    found[slot.key] = slot
    return sorted(found.values(), key=lambda s: s.starts)


# ------------------------------------------------------------------ the order (state machine)

STANDBY, LIVE = "standby", "live"


@dataclass(frozen=True)
class Order:
    command: str  # standby | live
    source: str | None  # schedule | manual | None
    code: str  # machine-readable reason: idle, slot, manual, slot_stopped, held, other_device
    current: dict | None
    next: dict | None

    def as_dict(self) -> dict:
        return asdict(self)


def decide(
    now: float,
    slots: list[Slot],
    manual: dict | None,
    stopped_keys: set[str],
    announced: dict[str, dict],
    margin_s: int,
) -> Order:
    """Pure decision: see the module docstring for the rules."""
    if manual is not None:
        return Order(
            LIVE,
            "manual",
            "manual",
            {
                "source": "manual",
                "type": manual["kind"],
                "title": manual["title"],
                "started_at": _iso(manual["started_at"]),
            },
            _next(now, slots, stopped_keys, announced, skip_running=False),
        )
    for s in slots:
        running = s.starts.timestamp() - margin_s <= now < s.ends.timestamp()
        if running and s.key in stopped_keys:
            return Order(STANDBY, None, "slot_stopped", None, _next(now, slots, stopped_keys, announced, after=s))
        if running:
            over = announced.get(s.key) or {}
            cur = s.as_dict(now) | {"source": "schedule"}
            cur["type"], cur["title"] = over.get("kind") or s.kind, over.get("title") or s.title
            return Order(LIVE, "schedule", "slot", cur, _next(now, slots, stopped_keys, announced, after=s))
    return Order(STANDBY, None, "idle", None, _next(now, slots, stopped_keys, announced))


def _next(now, slots, stopped_keys, announced, after: Slot | None = None, skip_running: bool = True) -> dict | None:
    for s in slots:
        if s.starts.timestamp() <= now or s.key in stopped_keys or (after is not None and s.key == after.key):
            continue
        over = announced.get(s.key) or {}
        return s.as_dict(now) | {"type": over.get("kind") or s.kind, "title": over.get("title") or s.title}
    return None


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ what the server remembers


def manual_run(conn, mosque_id: int, now: float | None = None) -> dict | None:
    """The manual live in progress, if any. One whose device went silent is closed here (and returned as None)."""
    now = time.time() if now is None else now
    run = orm.first(conn, StudioRun, StudioRun.ended_at.is_(None), mosque_id=mosque_id, origin="manual")
    if run is None:
        return None
    seen = conn.scalar(select(Device.last_seen_at).where(Device.id == run["device_id"])) or run["started_at"]
    if now - seen > MANUAL_SILENCE_S:
        conn.execute(update(StudioRun).where(StudioRun.id == run["id"]).values(ended_at=now))
        orm.done(conn)
        db.audit(
            conn,
            mosque_id,
            None,
            "studio.direct.manuel.fin",
            "appareil",
            run["device_id"],
            motif="silence",
            titre=run["title"],
        )
        return None
    return run


def _marks(conn, mosque_id: int, keys: list[str]) -> tuple[set[str], dict[str, dict]]:
    stopped, announced = set(), {}
    if keys:
        for r in orm.rows(
            conn, select(StudioRun).where(StudioRun.mosque_id == mosque_id, StudioRun.slot_key.in_(keys))
        ):
            if r["origin"] == "slot_stop":
                stopped.add(r["slot_key"])
            elif r["origin"] == "announce":
                announced[r["slot_key"]] = {"kind": r["kind"], "title": r["title"]}
    return stopped, announced


def current_order(conn, mosque: dict, now: float | None = None) -> tuple[Order, list[Slot]]:
    now = time.time() if now is None else now
    slots = upcoming(conn, mosque, now)
    stopped, announced = _marks(conn, mosque["id"], [s.key for s in slots])
    manual = manual_run(conn, mosque["id"], now)
    margin = settings(conn, mosque["id"])["start_margin_s"]
    return decide(now, slots, manual, stopped, announced, margin), slots


def running_slot(order: Order, slots: list[Slot]) -> Slot | None:
    if order.current and order.current.get("source") == "schedule":
        return next((s for s in slots if s.key == order.current["slot_id"]), None)
    return None


def stop_marks(conn, mosque_id: int, slot_key: str, now: float) -> None:
    conn.execute(
        delete(StudioRun).where(
            StudioRun.mosque_id == mosque_id, StudioRun.slot_key == slot_key, StudioRun.origin == "slot_stop"
        )
    )
    conn.add(StudioRun(mosque_id=mosque_id, origin="slot_stop", slot_key=slot_key, started_at=now, ended_at=now))
    orm.done(conn)


def announce_slot(conn, mosque_id: int, device_id: int, slot_key: str, kind: str, title: str, now: float) -> None:
    conn.execute(
        delete(StudioRun).where(
            StudioRun.mosque_id == mosque_id, StudioRun.slot_key == slot_key, StudioRun.origin == "announce"
        )
    )
    conn.add(
        StudioRun(
            mosque_id=mosque_id,
            device_id=device_id,
            origin="announce",
            kind=kind,
            title=title,
            slot_key=slot_key,
            started_at=now,
            ended_at=now,
        )
    )
    orm.done(conn)


def mosque_row(conn, mosque_id: int) -> dict | None:
    return orm.first(conn, Mosque, id=mosque_id)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
