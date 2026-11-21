"""What the studio device is told, and the manual live (start, stop, announce).

`order_for` turns the mosque's order (`studio_plan.decide`) into the order of *this* device: only one device
per mosque is designated to publish, a device told to stay quiet (taken over) stays in standby, and a
standby always says why. The manual commands write to the journal and answer 409 with the reason when the
mosque already has a live from another source.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select, update

from ..i18n import _
from . import db, live_watch, orm, studio_devices, studio_plan, studio_publisher
from .models import Device, StudioClaim, StudioRun

TYPES = ("khutba", "dars", "conference")


class Conflict(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(body.get("error", ""))
        self.status, self.body = status, body


def reason_text(code: str, **kw) -> str:
    return {
        "idle": _("No live planned for now."),
        "slot": _("A live is planned: start broadcasting."),
        "manual": _("A live started from the device is in progress."),
        "slot_stopped": _("This live was stopped by hand; it will not restart by itself."),
        "held": _("An administrator took the stream over: the device stays quiet for now."),
        "other_device": _("Another device of the mosque ({name}) takes care of the live.", name=kw.get("name", "?")),
    }[code]


@dataclass(frozen=True)
class DeviceOrder:
    order: studio_plan.Order
    slots: list
    other: str = ""

    def body(self) -> dict:
        o = self.order
        return {
            "command": o.command,
            "reason_code": o.code,
            "reason": reason_text(o.code, name=self.other),
            "current": o.current,
            "next": o.next,
        }


def _designated(conn, mosque_id: int, order: studio_plan.Order, dev: dict, now: float) -> dict:
    """The device that publishes: the manual live's, else the holder of the stream, else the oldest online."""
    devices = [
        d
        for d in orm.rows(conn, select(Device).where(Device.mosque_id == mosque_id, Device.revoked_at.is_(None)))
        if d["held_until"] <= now and (d["id"] == dev["id"] or studio_devices.is_online(d, now))
    ]
    by_id = {d["id"]: d for d in devices}
    if order.source == "manual":
        run = orm.first(conn, StudioRun, StudioRun.ended_at.is_(None), mosque_id=mosque_id, origin="manual")
        if run and run["device_id"] in by_id:
            return by_id[run["device_id"]]
    claim = orm.first(conn, StudioClaim, mosque_id=mosque_id)
    if claim and claim["device_id"] in by_id:
        return by_id[claim["device_id"]]
    return min(devices, key=lambda d: d["id"]) if devices else dev


def order_for(conn, mosque: dict, dev: dict, now: float | None = None) -> DeviceOrder:
    now = time.time() if now is None else now
    order, slots = studio_plan.current_order(conn, mosque, now)
    if dev["held_until"] > now:
        return DeviceOrder(studio_plan.Order(studio_plan.STANDBY, None, "held", None, order.next), slots)
    if order.command == studio_plan.LIVE:
        chosen = _designated(conn, mosque["id"], order, dev, now)
        if chosen["id"] != dev["id"]:
            return DeviceOrder(
                studio_plan.Order(studio_plan.STANDBY, None, "other_device", None, order.next), slots, chosen["name"]
            )
    return DeviceOrder(order, slots)


def who_of(dev: dict) -> studio_publisher.Who:
    return studio_publisher.Who("device", device_id=dev["id"], label=dev["name"])


# ------------------------------------------------------------------ manual live


def _guard_free(conn, mosque: dict, dev: dict) -> None:
    holder = studio_publisher.current(conn, mosque)
    if holder is not None and not (holder["known"] and who_of(dev).same_as(holder)):
        raise Conflict(409, studio_publisher.refusal(holder))


def start_manual(
    conn, mosque: dict, dev: dict, kind: str, title: str, ip: str | None, now: float | None = None
) -> dict:
    now = time.time() if now is None else now
    kind = str(kind or "").strip().lower()
    title = " ".join(str(title or "").split())[:200]
    if kind not in TYPES:
        raise Conflict(
            422,
            {"error": _("Unknown type: choose khutba, dars or conference."), "code": "bad_type"},
        )
    if dev["held_until"] > now:
        raise Conflict(409, {"error": reason_text("held"), "code": "held"})
    _guard_free(conn, mosque, dev)
    run = studio_plan.manual_run(conn, mosque["id"], now)
    if run is not None and run["device_id"] != dev["id"]:
        other = orm.first(conn, Device, id=run["device_id"]) or {}
        raise Conflict(
            409,
            {
                "error": _(
                    "A live is already running from {source}.",
                    source=_("the device “{name}”", name=other.get("name", "?")),
                ),
                "code": "live_active",
                "source": {"kind": "device", "label": other.get("name", ""), "since": run["started_at"]},
            },
        )
    if run is None:
        conn.add(
            StudioRun(
                mosque_id=mosque["id"], device_id=dev["id"], origin="manual", kind=kind, title=title, started_at=now
            )
        )
        orm.done(conn)
        db.audit(
            conn, mosque["id"], None, "studio.direct.manuel.debut", "appareil", dev["id"],
            ip=ip, type=kind, titre=title, appareil=dev["name"],
        )  # fmt: skip
    else:  # the same device asks again (retry): its live goes on, the announcement is refreshed
        conn.execute(update(StudioRun).where(StudioRun.id == run["id"]).values(kind=kind, title=title))
        orm.done(conn)
    return answer(conn, mosque, dev, now)


def stop(conn, mosque: dict, dev: dict, ip: str | None, now: float | None = None) -> dict:
    """Manual stop: ends the manual live and the scheduled slot running now, which then does not restart."""
    now = time.time() if now is None else now
    order, slots = studio_plan.current_order(conn, mosque, now)
    stopped = []
    run = studio_plan.manual_run(conn, mosque["id"], now)
    if run is not None:
        conn.execute(update(StudioRun).where(StudioRun.id == run["id"]).values(ended_at=now))
        orm.done(conn)
        stopped.append("manual")
        db.audit(
            conn, mosque["id"], None, "studio.direct.manuel.fin", "appareil", dev["id"],
            ip=ip, motif="arret", titre=run["title"], appareil=dev["name"],
        )  # fmt: skip
        order, slots = studio_plan.current_order(conn, mosque, now)
    slot = studio_plan.running_slot(order, slots)
    if slot is not None:
        studio_plan.stop_marks(conn, mosque["id"], slot.key, now)
        stopped.append("slot")
        db.audit(
            conn, mosque["id"], None, "studio.creneau.arret", "appareil", dev["id"],
            ip=ip, creneau=slot.key, titre=slot.title, appareil=dev["name"],
        )  # fmt: skip
    claim = orm.first(conn, StudioClaim, mosque_id=mosque["id"])
    if claim and claim["device_id"] == dev["id"]:
        live_watch.kick(studio_publisher.path_of(mosque))  # the stream ends even if the app forgets to close it
        studio_publisher.release(conn, mosque["id"])
    return {"stopped": stopped} | answer(conn, mosque, dev, now)


def announce(conn, mosque: dict, dev: dict, kind: str, title: str, ip: str | None, now: float | None = None) -> dict:
    """Type and title of the live in progress (kept with it, for the replay)."""
    now = time.time() if now is None else now
    kind = str(kind or "").strip().lower()
    title = " ".join(str(title or "").split())[:200]
    if kind not in TYPES:
        raise Conflict(422, {"error": _("Unknown type: choose khutba, dars or conference."), "code": "bad_type"})
    order, slots = studio_plan.current_order(conn, mosque, now)
    if order.command != studio_plan.LIVE:
        raise Conflict(409, {"error": _("No live is in progress to announce."), "code": "no_live"})
    if order.source == "manual":
        run = studio_plan.manual_run(conn, mosque["id"], now)
        conn.execute(update(StudioRun).where(StudioRun.id == run["id"]).values(kind=kind, title=title))
        orm.done(conn)
    else:
        studio_plan.announce_slot(conn, mosque["id"], dev["id"], order.current["slot_id"], kind, title, now)
    db.audit(
        conn, mosque["id"], None, "studio.direct.annonce", "appareil", dev["id"],
        ip=ip, type=kind, titre=title, source=order.source, appareil=dev["name"],
    )  # fmt: skip
    return answer(conn, mosque, dev, now)


def answer(conn, mosque: dict, dev: dict, now: float) -> dict:
    return order_for(conn, mosque, dev, now).body()


def current_announcement(conn, mosque: dict, now: float | None = None) -> dict:
    """What the orchestrator reads to attach type and title to the replay: {live, source, type, title}."""
    order, _slots = studio_plan.current_order(conn, mosque, now)
    cur = order.current or {}
    return {
        "live": order.command == studio_plan.LIVE,
        "source": cur.get("source"),
        "type": cur.get("type"),
        "title": cur.get("title"),
        "slot_id": cur.get("slot_id"),
    }
