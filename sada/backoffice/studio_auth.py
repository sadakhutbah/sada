"""Who calls a studio route: a signed-in person (session cookie, CSRF, `live` right) or a paired device.

Two ways in, one interface (`Actor`): the routes of the studio (token, WHIP, and the studio API of the
device) do not care which. A device only ever opens what is on this list; nothing else of the back office
accepts its credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from ..i18n import _
from . import db, orm, studio_devices
from .context import ConnDep, Ctx, DeviceRefused, get_ctx
from .models import Mosque
from .studio_publisher import Who


@dataclass
class Actor:
    kind: str  # user | device
    conn: object
    mosque: dict
    ip: str | None
    ctx: Ctx | None = None
    device: dict | None = None

    @property
    def user(self) -> dict | None:
        return self.ctx.user if self.ctx else None

    @property
    def user_id(self) -> int | None:
        return self.ctx.user["id"] if self.ctx else None

    def need_live(self, write: bool = True) -> None:
        """A person: CSRF (writes) and the `live` right. A device: its credential was the proof."""
        if self.ctx:
            if write:
                self.ctx.need_csrf()
            self.ctx.require("live")

    def who(self) -> Who:
        if self.device:
            return Who("device", device_id=self.device["id"], label=self.device["name"])
        return Who("regie", user_id=self.user_id, label=self.ctx.user["display_name"] or self.ctx.user["username"])

    def audit(self, action: str, **details) -> None:
        if self.device:
            details.setdefault("appareil", self.device["name"])
        db.audit(self.conn, self.mosque["id"], self.user_id, action, "mosque", self.mosque["id"], ip=self.ip, **details)


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or ""
    return h if h.lower().startswith("bearer sdev_") else None


def get_device_actor(request: Request, conn: ConnDep) -> Actor:
    """Device only (heartbeat, live start/stop/announce)."""
    from .throttle import client_ip

    ip = client_ip(request) if request.client else None
    header = bearer(request)
    if header is None:
        raise DeviceRefused("invalid", _("device credential missing or malformed"))
    dev = studio_devices.authenticate(conn, header, ip)
    return Actor("device", conn, orm.first(conn, Mosque, id=dev["mosque_id"]), ip, device=dev)


def get_actor(request: Request, conn: ConnDep) -> Actor:
    """Device when a device credential is presented, otherwise the signed-in person."""
    if bearer(request) is not None:
        return get_device_actor(request, conn)
    ctx = get_ctx(request, conn)
    return Actor("user", conn, ctx.mosque, ctx.ip, ctx=ctx)


ActorDep = Annotated[Actor, Depends(get_actor)]
DeviceDep = Annotated[Actor, Depends(get_device_actor)]
