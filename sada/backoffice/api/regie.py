"""Control room (“Régie”): live state for the page, and the two monitors proxied to the relay.

Every route needs a session open on a mosque and the `live` permission. The monitors never take a
stream name from the request: the path comes from the session's mosque (live_watch.path_of).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ...i18n import _
from .. import live, live_input, live_watch, studio_plan, studio_publisher, studio_service, studio_token
from .. import screens as screens_mod
from ..context import CtxDep, NotFound
from ..studio_auth import Actor, ActorDep

router = APIRouter()

# The control room refreshes every few seconds: a fresher snapshot than the dashboard's.
STATUS_TTL = 3.0
MAX_SDP = 64 * 1024


def _path(ctx, which: str) -> str:
    if which not in live_watch.SOURCES:
        raise NotFound()
    return live_watch.path_of(ctx.conn, ctx.mosque, which) or ""


def _offline() -> JSONResponse:
    return JSONResponse({"error": "offline"}, status_code=404)


@router.get("/live/regie")
def regie(ctx: CtxDep):
    ctx.require("live")
    paths = {w: live_watch.path_of(ctx.conn, ctx.mosque, w) for w in live_watch.SOURCES}
    health = {w: live_watch.health(p) for w, p in paths.items()}
    holder = studio_publisher.current(ctx.conn, ctx.mosque)
    return {
        "publisher": holder and {k: holder[k] for k in ("source", "label", "since", "text")},
        "live": live.status_cached(ctx.mosque, ttl=STATUS_TTL),
        "commands": [
            {"key": k, "destructive": k in live.DESTRUCTIVE, "choices": list(live.choices(k))} for k in live.COMMANDS
        ],
        # A monitor is only connected once its stream is received: an empty path (programme while the
        # engine is off, preview while OBS is not sending) would answer 404 on every retry.
        "monitors": {
            w: {"available": bool(p) and (health[w] is None or bool(health[w].get("online"))), "health": health[w]}
            for w, p in paths.items()
        },
    }


@router.get("/live/regie/scene/{name}.png")
async def scene_preview(name: str, ctx: CtxDep):
    """Studio mode: the scene as it would go on air, with the mosque's saved texts. Banner scenes come
    without background, to be laid over the preview feed."""
    ctx.require("live")
    if name not in live.choices("scene"):
        raise NotFound()
    textes = screens_mod.saved(ctx.conn, ctx.mosque["id"])
    png = await run_in_threadpool(screens_mod.preview, ctx.mosque, textes, name, True, ctx.conn)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=30"})


@router.post("/live/watch/{which}/whep")
async def whep_offer(which: str, request: Request, ctx: CtxDep):
    ctx.need_csrf()
    ctx.require("live")
    path = _path(ctx, which)
    if not path:
        return _offline()
    sdp = await request.body()
    if not sdp or len(sdp) > MAX_SDP:
        return JSONResponse({"error": "bad offer"}, status_code=400)
    try:
        status, answer, session = await live_watch.whep_offer(path, sdp)
    except live_watch.RelayUnavailable:
        return JSONResponse({"error": "relay unavailable"}, status_code=503)
    if status != 201 or not session:
        # 404 from the relay: nothing is published on this path right now
        return _offline() if status in (400, 404) else JSONResponse({"error": "relay"}, status_code=502)
    return Response(
        answer,
        status_code=201,
        media_type="application/sdp",
        headers={"Location": f"/api/v1/live/watch/{which}/whep/{session}", "Cache-Control": "no-store"},
    )


@router.delete("/live/watch/{which}/whep/{session}")
async def whep_close(which: str, session: str, ctx: CtxDep):
    ctx.need_csrf()
    ctx.require("live")
    path = _path(ctx, which)
    if not path or not live_watch.WHEP_SESSION.match(session):
        raise NotFound()
    try:
        await live_watch.whep_close(path, session)
    except live_watch.RelayUnavailable:
        pass  # the relay is gone, and the session with it
    return Response(status_code=200)


@router.get("/live/watch/{which}/hls/{name}")
async def hls(which: str, name: str, ctx: CtxDep, session: str | None = None):
    ctx.require("live")
    path = _path(ctx, which)
    if not path or not live_watch.HLS_FILE.match(name):
        return _offline()
    try:
        status, body, ctype = await live_watch.hls_file(path, name, session)
    except live_watch.RelayUnavailable:
        return JSONResponse({"error": "relay unavailable"}, status_code=503)
    if status != 200:
        return _offline()
    return Response(body, media_type=ctype, headers={"Cache-Control": "no-store"})


# --- Studio: publish from the browser or from a paired device (WHIP), through the back office ------------
# The relay's WebRTC port is never reached by the client for signalling: the offer goes through here,
# behind the session and the `live` right (or a device credential), and carries a short-lived token bound to
# the user or the device and to the mosque. The stream name comes from the mosque, never from the request.

WHIP_SESSION = live_watch.WHEP_SESSION
ICE_TYPE = "application/trickle-ice-sdpfrag"


def _studio_path(a: Actor) -> str:
    return f"live/{live_input.slug(a.mosque)}"


def _studio_token(a: Actor) -> str:
    return studio_token.issue(a.user_id, a.mosque, device_id=a.device["id"] if a.device else None)[0]


def _need_push(a: Actor) -> JSONResponse | None:
    """The studio feeds the mosque's push input: a camera input is read by the relay itself."""
    if live_input.current(a.conn, a.mosque)["mode"] != "push":
        return JSONResponse({"error": "input is not push"}, status_code=409)
    return None


def _may_start(a: Actor) -> JSONResponse | None:
    """Before a token or an offer: the device is ordered to broadcast, and nobody else holds the stream."""
    if a.device:
        o = studio_service.order_for(a.conn, a.mosque, a.device)
        if o.order.command != studio_plan.LIVE:
            body = o.body() | {"error": o.body()["reason"], "code": "not_ordered"}
            return JSONResponse(body, status_code=409)
    holder = studio_publisher.current(a.conn, a.mosque)
    if holder is not None and not (holder["known"] and a.who().same_as(holder)):
        return JSONResponse(studio_publisher.refusal(holder), status_code=409)
    return None


@router.post("/live/studio/token")
def studio_token_issue(a: ActorDep):
    """Token to start a studio session: bound to the signed-in user (or the device) and the mosque."""
    a.need_live()
    if (refused := _need_push(a) or _may_start(a)) is not None:
        return refused
    token, life = studio_token.issue(a.user_id, a.mosque, device_id=a.device["id"] if a.device else None)
    a.audit("studio.jeton")
    return JSONResponse(
        {"token": token, "expires_in": life, "path": _studio_path(a)}, headers={"Cache-Control": "no-store"}
    )


@router.post("/live/studio/whip")
async def whip_offer(request: Request, a: ActorDep):
    a.need_live()
    if (refused := _need_push(a) or _may_start(a)) is not None:
        return refused
    sdp = await request.body()
    if not sdp or len(sdp) > MAX_SDP:
        return JSONResponse({"error": "bad offer"}, status_code=400)
    try:
        r = await live_watch.whip_request("POST", _studio_path(a), _studio_token(a), body=sdp, ctype="application/sdp")
    except live_watch.RelayUnavailable:
        return JSONResponse({"error": "relay unavailable"}, status_code=503)
    session = (r.headers.get("location") or "").rstrip("/").rsplit("/", 1)[-1]
    if r.status_code in (401, 403):
        holder = studio_publisher.current(a.conn, a.mosque)  # the relay refused: say why when somebody holds the stream
        if holder is not None and not (holder["known"] and a.who().same_as(holder)):
            return JSONResponse(studio_publisher.refusal(holder), status_code=409)
        return JSONResponse({"error": "relay refused"}, status_code=502)
    if r.status_code != 201 or not WHIP_SESSION.match(session):
        return JSONResponse({"error": "relay"}, status_code=409 if r.status_code == 400 else 502)
    a.audit("studio.whip.debut", session=session)
    return Response(
        r.content,
        status_code=201,
        media_type="application/sdp",
        headers={
            "Location": f"/api/v1/live/studio/whip/{session}",
            "Cache-Control": "no-store",
            "Accept-Patch": ICE_TYPE,
        },
    )


@router.patch("/live/studio/whip/{session}")
async def whip_ice(session: str, request: Request, a: ActorDep):
    a.need_live()
    if not WHIP_SESSION.match(session):
        raise NotFound()
    body = await request.body()
    if len(body) > MAX_SDP:
        return JSONResponse({"error": "bad request"}, status_code=400)
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != ICE_TYPE:
        return JSONResponse({"error": "unsupported"}, status_code=415)
    try:
        r = await live_watch.whip_request("PATCH", _studio_path(a), _studio_token(a), session, body, ICE_TYPE)
    except live_watch.RelayUnavailable:
        return JSONResponse({"error": "relay unavailable"}, status_code=503)
    if r.status_code == 404:
        raise NotFound()
    return Response(status_code=204 if r.status_code in (200, 204) else 502)


@router.delete("/live/studio/whip/{session}")
async def whip_close(session: str, a: ActorDep):
    a.need_live()
    if not WHIP_SESSION.match(session):
        raise NotFound()
    try:
        await live_watch.whip_request("DELETE", _studio_path(a), _studio_token(a), session)
    except live_watch.RelayUnavailable:
        pass  # the relay is gone, and the session with it
    a.audit("studio.whip.fin", session=session)
    return Response(status_code=200)


@router.post("/live/studio/takeover")
async def studio_takeover(ctx: CtxDep):
    """Explicit resumption, for an administrator of the mosque: cuts the stream that is running now.

    The body must say `{"confirm": true}` (the console asks first). The old publisher is kicked; a device
    displaced this way stays quiet for a while; the journal keeps who, when and whom it displaced."""
    body = await ctx.raw_body()  # CSRF
    ctx.require("admin")
    ctx.require("live")
    if body.get("confirm") not in (True, "true", "1", 1):
        return JSONResponse({"error": _("Confirmation required."), "code": "confirm"}, status_code=422)
    holder = await run_in_threadpool(studio_publisher.takeover, ctx.conn, ctx.mosque, ctx.user, ctx.ip)
    if holder is None:
        return JSONResponse(
            {"error": _("No live is running: nothing to take over."), "code": "no_live"}, status_code=409
        )
    return {
        "ok": True,
        "message": _("The stream of {source} was cut. You can publish now.", source=holder["text"]),
        "previous": {"source": holder["source"], "label": holder["label"]},
    }
