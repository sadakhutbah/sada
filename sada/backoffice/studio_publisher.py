"""One publisher per mosque.

The relay refuses a second publisher on a path (`overridePublisher: no`); this module adds what the relay
cannot say: *who* holds the stream (a studio device, the mosque's OBS key, or the control room), a clear
reason for the refused one, the audit trail, and the explicit takeover reserved to an administrator.

`admit` is asked for every publish (publish-auth and the studio token routes). The holder is remembered in
`studio_claims` when a publication is accepted; the relay says whether the path is really live.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import delete, update

from ..i18n import _
from . import db, live_input, live_watch, orm
from .models import Device, StudioClaim, StudioRun, User
from .studio_devices import HOLD_S

SOURCES = ("device", "obs", "regie")


@dataclass(frozen=True)
class Who:
    """Whoever asks to publish."""

    source: str  # device | obs | regie
    device_id: int | None = None
    user_id: int | None = None
    label: str = ""

    def same_as(self, claim: dict) -> bool:
        if self.source != claim["source"]:
            return False
        if self.source == "device":
            return self.device_id == claim["device_id"]
        if self.source == "regie":
            return self.user_id == claim["user_id"]
        return True  # the OBS key is one holder: its own reconnection


def path_of(mosque: dict) -> str:
    return f"live/{live_input.slug(mosque)}"


def source_label(claim: dict | None) -> str:
    """Human wording of a holder, for “A live is already running from <source>”."""
    if not claim:
        return _("an unknown source")
    if claim["source"] == "device":
        return _("the device “{name}”", name=claim["label"] or "?")
    if claim["source"] == "regie":
        return _("the control room ({name})", name=claim["label"] or "?")
    return _("the mosque's OBS stream key")


def current(conn, mosque: dict) -> dict | None:
    """The live publisher of the mosque, or None. {source, device_id, user_id, label, since, text, known}.

    The relay is the judge of whether a stream exists; when its API does not answer nothing is claimed
    (the relay itself still refuses a second publisher)."""
    info = live_watch.publisher_info(path_of(mosque))
    if not info or not info["ready"]:
        return None
    claim = orm.first(conn, StudioClaim, mosque_id=mosque["id"])
    if claim and claim["device_id"]:
        dev = orm.first(conn, Device, id=claim["device_id"])
        claim["label"] = dev["name"] if dev else claim["label"]
    out = (
        dict(claim) if claim else {"source": "unknown", "device_id": None, "user_id": None, "label": "", "since": None}
    )
    out["text"] = source_label(claim)
    out["known"] = claim is not None
    return out


def refusal(holder: dict) -> dict:
    return {
        "error": _("A live is already running from {source}.", source=holder["text"]),
        "code": "live_active",
        "source": {"kind": holder["source"], "label": holder["label"], "since": holder["since"]},
    }


def admit(conn, mosque: dict, who: Who) -> tuple[bool, dict | None]:
    """(allowed, holder). The same holder reconnecting replaces its own stale session (kicked)."""
    holder = current(conn, mosque)
    if holder is None:
        return True, None
    if holder["known"] and who.same_as(holder):
        live_watch.kick(path_of(mosque))
        return True, holder
    return False, holder


def record(conn, mosque: dict, who: Who, now: float | None = None) -> None:
    orm.upsert(
        conn,
        StudioClaim,
        ("mosque_id",),
        mosque_id=mosque["id"],
        source=who.source,
        device_id=who.device_id,
        user_id=who.user_id,
        label=who.label,
        since=time.time() if now is None else now,
    )


def release(conn, mosque_id: int) -> None:
    conn.execute(delete(StudioClaim).where(StudioClaim.mosque_id == mosque_id))
    orm.done(conn)


def takeover(conn, mosque: dict, user: dict, ip: str | None, now: float | None = None) -> dict | None:
    """Administrator's explicit resumption: cuts the current publisher. None when there was nobody."""
    now = time.time() if now is None else now
    holder = current(conn, mosque)
    if holder is None:
        return None
    live_watch.kick(path_of(mosque))
    release(conn, mosque["id"])
    if holder["device_id"]:
        # displaced device: told to stay quiet, its manual live (if any) is over
        conn.execute(update(Device).where(Device.id == holder["device_id"]).values(held_until=now + HOLD_S))
        conn.execute(
            update(StudioRun)
            .where(StudioRun.mosque_id == mosque["id"], StudioRun.origin == "manual", StudioRun.ended_at.is_(None))
            .values(ended_at=now)
        )
        orm.done(conn)
    db.audit(
        conn,
        mosque["id"],
        user["id"],
        "studio.reprise",
        "mosque",
        mosque["id"],
        ip=ip,
        ancienne_source=holder["source"],
        appareil=holder["device_id"],
        libelle=holder["label"],
    )
    return holder


def user_label(conn, user_id: int | None) -> str:
    u = orm.first(conn, User, id=user_id) if user_id else None
    return (u or {}).get("display_name") or (u or {}).get("username") or ""
