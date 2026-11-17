"""Control room (“Régie”): live state for the page, and the two monitors proxied to the relay.

Every route needs a session open on a mosque and the `live` permission. The monitors never take a
stream name from the request: the path comes from the session's mosque (live_watch.path_of).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .. import live, live_watch
from .. import screens as screens_mod
from ..context import CtxDep, NotFound

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
    return {
        "live": live.status_cached(ctx.mosque, ttl=STATUS_TTL),
        "commands": [
            {"key": k, "destructive": k in live.DESTRUCTIVE, "choices": list(live.choices(k))} for k in live.COMMANDS
        ],
        "monitors": {w: {"available": bool(p), "health": health[w]} for w, p in paths.items()},
    }


@router.get("/live/regie/scene/{name}.png")
async def scene_preview(name: str, ctx: CtxDep):
    """Studio mode: the scene as it would go on air, with the mosque's saved texts. Banner scenes come
    without background, to be laid over the preview feed."""
    ctx.require("live")
    if name not in live.choices("scene"):
        raise NotFound()
    textes = screens_mod.saved(ctx.conn, ctx.mosque["id"])
    png = await run_in_threadpool(screens_mod.preview, ctx.mosque, textes, name, True)
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
