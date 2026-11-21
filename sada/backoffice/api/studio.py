"""Studio devices: pairing, heartbeat, manual live, and the console's “Devices” page.

Three kinds of caller, three kinds of door:

* public, rate-limited, nothing to steal: `pair/start`, `pair/poll`, the unsubscribe link of a mail;
* the paired device, with its credential as a bearer (`studio_auth.DeviceDep`): `heartbeat`, `live/start`,
  `live/stop`, `live/announce` (the studio token and WHIP routes are in `regie`);
* a signed-in person who manages the live (administrator or manager with the `live` right): the rest. The
  mosque is always the session's: a device or an incident of another mosque is simply not found.

See docs/studio-devices.md for the exact contract.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from ... import mailer
from ...i18n import _
from .. import (
    db,
    live_watch,
    orm,
    studio_devices,
    studio_incidents,
    studio_plan,
    studio_publisher,
    studio_service,
)
from ..context import ConnDep, Ctx, CtxDep, Forbidden, Invalid, NotFound
from ..models import Device, StudioClaim, StudioRun, User
from ..studio_auth import DeviceDep
from ..throttle import SlidingWindow, client_ip

router = APIRouter()

HEARTBEAT_S = 30

_start_limit = SlidingWindow(10, 60.0)  # new pairings per minute and address
_poll_limit = SlidingWindow(30, 60.0)
_approve_limit = SlidingWindow(10, 60.0)  # guesses of a code per minute and account


def reset() -> None:
    """For tests: forget the counters."""
    global _start_limit, _poll_limit, _approve_limit
    _start_limit, _poll_limit, _approve_limit = (
        SlidingWindow(10, 60.0),
        SlidingWindow(30, 60.0),
        SlidingWindow(10, 60.0),
    )


def _too_many(wait: float) -> JSONResponse:
    return JSONResponse(
        {"error": _("too many requests"), "code": "rate_limited"},
        status_code=429,
        headers={"Retry-After": str(int(wait) + 1)},
    )


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — empty or unreadable body
        return {}
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ pairing (public)


@router.post("/studio/pair/start")
async def pair_start(request: Request, conn: ConnDep):
    wait = _start_limit.hit(client_ip(request))
    if wait:
        return _too_many(wait)
    body = await _json(request)
    out = studio_devices.start_pairing(conn, client_ip(request), body.get("name", ""))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/studio/pair/poll")
async def pair_poll(request: Request, conn: ConnDep):
    wait = _poll_limit.hit(client_ip(request))
    if wait:
        return _too_many(wait)
    body = await _json(request)
    status, out = studio_devices.poll(conn, str(body.get("device_code") or ""))
    return JSONResponse(out, status_code=status, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ heartbeat and manual live (device)


@router.post("/studio/heartbeat")
async def heartbeat(request: Request, a: DeviceDep):
    body = await _json(request)
    now = time.time()
    studio_devices.record_heartbeat(a.conn, a.device, body, now)
    a.device.update(last_seen_at=now, held_until=a.device["held_until"])
    new_credential = studio_devices.rotate_if_requested(a.conn, a.device, now)
    if new_credential:
        a.audit("studio.appareil.rotation", appareil_id=a.device["id"])
    out = studio_service.order_for(a.conn, a.mosque, a.device, now)
    cfg = studio_plan.settings(a.conn, a.mosque["id"])
    resp = out.body() | {
        "server_time": studio_plan._iso(now),
        "config": {
            "name": a.device["name"],
            "mosque": {"name": a.mosque["name"], "slug": a.mosque["slug"], "timezone": a.mosque["timezone"]},
            "heartbeat_interval_s": HEARTBEAT_S,
            "start_margin_s": cfg["start_margin_s"],
            "token_path": "/api/v1/live/studio/token",
            "whip_path": "/api/v1/live/studio/whip",
        },
        "slots": [s.as_dict(now) for s in out.slots[:5]],
    }
    if new_credential:
        resp["credentials"] = {"device_id": studio_devices.public_id(a.device["id"]), "device_secret": new_credential}
    return JSONResponse(resp, headers={"Cache-Control": "no-store"})


@router.post("/studio/unpair")
async def unpair(request: Request, a: DeviceDep):
    """The device leaves the mosque by itself (“Forget this mosque” in the application): same effect as an
    administrator's revocation (credential refused at once, stream cut, manual live ended), journaled as such."""
    await _json(request)
    dev, mosque = a.device, a.mosque
    studio_devices.revoke(a.conn, dev, None)
    cut = False
    claim = orm.first(a.conn, StudioClaim, mosque_id=mosque["id"])
    if claim and claim["device_id"] == dev["id"]:
        cut = live_watch.kick(studio_publisher.path_of(mosque))
        studio_publisher.release(a.conn, mosque["id"])
    a.conn.execute(
        StudioRun.__table__.update()
        .where(StudioRun.device_id == dev["id"], StudioRun.ended_at.is_(None), StudioRun.origin == "manual")
        .values(ended_at=time.time())
    )
    orm.done(a.conn)
    a.audit("studio.appareil.desappaire", appareil_id=dev["id"], flux_coupe=cut)
    return JSONResponse({"unpaired": True, "stream_cut": cut}, headers={"Cache-Control": "no-store"})


def _manual(call):
    """Runs a manual command: a refusal carries its reason (409 live_active, held, no_live; 422 bad_type)."""
    try:
        out = call()
    except studio_service.Conflict as c:
        return JSONResponse(c.body, status_code=c.status)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/studio/live/start")
async def live_start(request: Request, a: DeviceDep):
    body = await _json(request)
    return _manual(
        lambda: studio_service.start_manual(a.conn, a.mosque, a.device, body.get("type"), body.get("title"), a.ip)
    )


@router.post("/studio/live/stop")
async def live_stop(request: Request, a: DeviceDep):
    return _manual(lambda: studio_service.stop(a.conn, a.mosque, a.device, a.ip))


@router.post("/studio/live/announce")
async def live_announce(request: Request, a: DeviceDep):
    body = await _json(request)
    return _manual(
        lambda: studio_service.announce(a.conn, a.mosque, a.device, body.get("type"), body.get("title"), a.ip)
    )


# ------------------------------------------------------------------ console


def _manage(ctx: Ctx, write: bool = False) -> None:
    """The Devices page is for the people who drive the live: the `live` right, as administrator or manager."""
    if write:
        ctx.need_csrf()
    if not studio_incidents.can_manage(ctx.user):
        db.audit(
            ctx.conn,
            ctx.mosque["id"],
            ctx.user["id"],
            "acces.refuse",
            ip=ctx.ip,
            chemin=ctx.request.url.path,
            droit="live",
        )
        raise Forbidden()


def _device_of(ctx: Ctx, did: int) -> dict:
    dev = orm.first(ctx.conn, Device, id=did, mosque_id=ctx.mosque["id"])  # never another mosque's
    if dev is None:
        raise NotFound()
    return dev


def _holder_view(holder: dict | None) -> dict | None:
    if holder is None:
        return None
    return {k: holder[k] for k in ("source", "label", "since", "text", "device_id")}


@router.get("/studio/devices")
def devices(ctx: CtxDep):
    _manage(ctx)
    now = time.time()
    mid = ctx.mosque["id"]
    rows = orm.rows(
        ctx.conn, select(Device).where(Device.mosque_id == mid).order_by(Device.revoked_at.is_not(None), Device.id)
    )
    names = studio_devices.creator_names(ctx.conn, rows)
    order, slots = studio_plan.current_order(ctx.conn, ctx.mosque, now)
    me = ctx.user
    return {
        "devices": [studio_devices.public(d, now) | {"created_by_name": names.get(d["created_by"], "")} for d in rows],
        "incidents": [studio_incidents.public(i) for i in studio_incidents.open_incidents(ctx.conn, mid)],
        "publisher": _holder_view(studio_publisher.current(ctx.conn, ctx.mosque)),
        "order": order.as_dict(),
        "slots": [s.as_dict(now) for s in slots[:10]],
        "settings": studio_plan.settings(ctx.conn, mid),
        "can_takeover": ctx.can("admin"),
        "mail": {"configured": mailer.configured(), "email": me["email"], "subscribed": not me["studio_mail_optout"]},
        "pair_ttl": studio_devices.PAIR_TTL_S,
        "mosque": {"name": ctx.mosque["name"], "slug": ctx.mosque["slug"]},
    }


@router.post("/studio/devices/pair")
async def device_pair(ctx: CtxDep):
    body = await ctx.body()
    _manage(ctx, write=False)
    wait = _approve_limit.hit(f"u{ctx.user['id']}")
    if wait:
        return _too_many(wait)
    try:
        dev = studio_devices.approve(ctx.conn, ctx.mosque, ctx.user, body.get("user_code", ""), body.get("name", ""))
    except studio_devices.PairingError as e:
        db.audit(
            ctx.conn,
            ctx.mosque["id"],
            ctx.user["id"],
            "studio.appairage.refuse",
            "mosque",
            ctx.mosque["id"],
            ip=ctx.ip,
            motif=e.code,
        )
        return JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "studio.appareil.appaire",
        "appareil",
        dev["id"],
        ip=ctx.ip,
        nom=dev["name"],
    )
    return {
        "ok": True,
        "message": _("Device “{name}” paired. It connects within a few seconds.", name=dev["name"]),
        "device": dev,
    }


@router.patch("/studio/devices/{did}")
async def device_rename(did: int, ctx: CtxDep):
    body = await ctx.body()
    _manage(ctx)
    dev = _device_of(ctx, did)
    name = " ".join(str(body.get("name") or "").split())[:60]
    if not name:
        raise Invalid(_("Give the device a name (for example “Mosque PC”)."))
    if studio_devices.device_name_taken(ctx.conn, ctx.mosque["id"], name, except_id=did):
        raise Invalid(_("A device of this mosque already has this name."))
    ctx.conn.execute(Device.__table__.update().where(Device.id == did).values(name=name))
    orm.done(ctx.conn)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "studio.appareil.renomme",
        "appareil",
        did,
        ip=ctx.ip,
        avant=dev["name"],
        apres=name,
    )
    return {"ok": True, "message": _("Device renamed."), "name": name}


@router.post("/studio/devices/{did}/revoke")
async def device_revoke(did: int, ctx: CtxDep):
    await ctx.body()
    _manage(ctx)
    dev = _device_of(ctx, did)
    if dev["revoked_at"]:
        return {"ok": True, "message": _("This device was already revoked.")}
    studio_devices.revoke(ctx.conn, dev, ctx.user["id"])
    cut = False
    claim = orm.first(ctx.conn, StudioClaim, mosque_id=ctx.mosque["id"])
    if claim and claim["device_id"] == did:
        cut = live_watch.kick(studio_publisher.path_of(ctx.mosque))  # its live in progress is cut, if the relay says so
        studio_publisher.release(ctx.conn, ctx.mosque["id"])
    ctx.conn.execute(
        StudioRun.__table__.update()
        .where(StudioRun.device_id == did, StudioRun.ended_at.is_(None), StudioRun.origin == "manual")
        .values(ended_at=time.time())
    )
    orm.done(ctx.conn)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "studio.appareil.revoque",
        "appareil",
        did,
        ip=ctx.ip,
        nom=dev["name"],
        flux_coupe=cut,
    )
    return {
        "ok": True,
        "message": _("Device “{name}” revoked: it is refused from now on.", name=dev["name"]),
        "stream_cut": cut,
    }


@router.post("/studio/devices/{did}/rotate")
async def device_rotate(did: int, ctx: CtxDep):
    await ctx.body()
    _manage(ctx)
    dev = _device_of(ctx, did)
    if dev["revoked_at"]:
        raise Invalid(_("This device is revoked."))
    studio_devices.request_rotation(ctx.conn, did)
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "studio.appareil.rotation.demandee",
        "appareil",
        did,
        ip=ctx.ip,
        nom=dev["name"],
    )
    return {"ok": True, "message": _("New key requested: the device receives it at its next signal (within a minute).")}


@router.get("/studio/settings")
def settings_read(ctx: CtxDep):
    _manage(ctx)
    return studio_plan.settings(ctx.conn, ctx.mosque["id"])


@router.put("/studio/settings")
async def settings_write(ctx: CtxDep):
    body = await ctx.raw_body()
    ctx.require("admin")
    try:
        cfg = studio_plan.save_settings(ctx.conn, ctx.mosque["id"], body)
    except ValueError as e:
        raise Invalid(str(e)) from e
    db.audit(
        ctx.conn, ctx.mosque["id"], ctx.user["id"], "studio.reglages", "mosque", ctx.mosque["id"], ip=ctx.ip, **cfg
    )
    return {"ok": True, "message": _("Studio settings saved."), "settings": cfg}


@router.get("/studio/schedule")
def schedule(ctx: CtxDep):
    _manage(ctx)
    now = time.time()
    order, slots = studio_plan.current_order(ctx.conn, ctx.mosque, now)
    return {"order": order.as_dict(), "slots": [s.as_dict(now) for s in slots], "timezone": ctx.mosque["timezone"]}


@router.put("/studio/alerts/preferences")
async def alert_preferences(ctx: CtxDep):
    """The signed-in account's own address for the studio checks, and its (un)subscription."""
    body = await ctx.raw_body()
    _manage(ctx)
    values: dict = {}
    if "email" in body:
        email = str(body.get("email") or "").strip()
        if email and (len(email) > 200 or "@" not in email.strip("@") or any(c in email for c in " \r\n<>,;")):
            raise Invalid(_("This e-mail address is not valid."))
        values["email"] = email
    if "subscribed" in body:
        values["studio_mail_optout"] = 0 if body["subscribed"] in (True, "true", "1", 1) else 1
    if values:
        ctx.conn.execute(User.__table__.update().where(User.id == ctx.user["id"]).values(**values))
        orm.done(ctx.conn)
        db.audit(
            ctx.conn,
            ctx.mosque["id"],
            ctx.user["id"],
            "studio.alertes.preferences",
            "compte",
            ctx.user["id"],
            ip=ctx.ip,
            abonne=values.get("studio_mail_optout") == 0 if "studio_mail_optout" in values else None,
        )
    return {"ok": True, "message": _("Alert preferences saved.")}


# ------------------------------------------------------------------ unsubscribe link of the mails (public)


@router.api_route("/studio/unsubscribe", methods=["GET", "POST"])
def unsubscribe(conn: ConnDep, u: int = 0, t: str = ""):
    import hmac

    user = orm.first(conn, User, id=u) if u else None
    if not user or not hmac.compare_digest(t or "-", studio_incidents.unsubscribe_token(u)):
        return HTMLResponse("<p>Lien invalide.</p>", status_code=400)
    conn.execute(User.__table__.update().where(User.id == u).values(studio_mail_optout=1))
    orm.done(conn)
    db.audit(conn, user["mosque_id"], u, "studio.alertes.desabonnement", "compte", u)
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Sada</title>"
        "<p>C'est fait : vous ne recevrez plus les messages de contrôle du studio. "
        "Vous pouvez vous réabonner depuis la page « Appareils » de la console.</p>"
    )


@router.get("/studio/incidents")
def incidents(ctx: CtxDep):
    _manage(ctx)
    return [studio_incidents.public(i) for i in studio_incidents.open_incidents(ctx.conn, ctx.mosque["id"])]
