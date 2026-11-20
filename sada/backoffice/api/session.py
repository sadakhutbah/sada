"""Login, logout and the session probe."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from .. import auth, db, orm
from ..context import (
    PRE_COOKIE,
    ConnDep,
    Ctx,
    Forbidden,
    PreCtxDep,
    _sec,
    clip_problems,
    count_clips,
    get_pre_ctx,
    open_mosque,
)
from ..models import Khutba, User
from ..throttle import client_ip
from .common import _mosque, _user


def _tarawih_nights(ctx) -> int:
    """Number of tarawih replays: the navigation shows their page only when there are some."""
    if not ctx.mosque:
        return 0
    q = select(func.count()).where(Khutba.mosque_id == ctx.mosque["id"], Khutba.kind == "tarawih")
    return int(ctx.conn.execute(q).scalar() or 0)


router = APIRouter()


def _secure(request: Request) -> bool:
    return request.app.state.secure or _sec(request)


@router.get("/login-challenge")
def challenge(request: Request):
    """Anti-replay token for login: bound to the browser by a cookie."""
    pre = secrets.token_urlsafe(16)
    r = JSONResponse({"pre": pre})
    r.set_cookie(PRE_COOKIE, pre, httponly=True, samesite="lax", secure=_secure(request), max_age=600, path="/")
    return r


@router.post("/login")
async def login(request: Request, conn: ConnDep):
    f = await request.form()
    username = str(f.get("username", "")).strip()
    password = str(f.get("password", ""))
    pre = request.cookies.get(PRE_COOKIE)
    ip = client_ip(request)
    if not pre or not secrets.compare_digest(pre, str(f.get("pre", ""))):
        return JSONResponse({"error": "challenge"}, status_code=403)
    lim = request.app.state.limiter
    if lim.blocked(ip, username):
        return JSONResponse({"error": "rate"}, status_code=429)
    u = await run_in_threadpool(auth.authenticate, conn, username, password)
    if not u:
        lim.fail(ip, username)
        connu = orm.first(conn, User, username=username)
        db.audit(conn, connu["mosque_id"] if connu else None, None, "connexion.echec", ip=ip, identifiant=username[:64])
        return JSONResponse({"error": "identifiants"}, status_code=401)
    lim.success(ip, username)
    token = auth.open_session(conn, u["id"], ip, request.headers.get("user-agent"))
    db.audit(conn, u["mosque_id"], u["id"], "connexion", ip=ip)
    sess = auth.load_session(conn, token, request.headers.get("user-agent"), ip)[0]
    if not auth.hosted():
        auth.choose_mosque(conn, sess, u, u["mosque_id"])
        sess["mosque_id"] = u["mosque_id"]
    ctx = Ctx(request, conn, sess, u, open_mosque(conn, sess, u))
    r = JSONResponse(_state(ctx))
    r.set_cookie(
        auth.COOKIE, token, httponly=True, samesite="lax", secure=_secure(request), max_age=auth.SESSION_TTL, path="/"
    )
    r.delete_cookie(PRE_COOKIE)
    return r


def _state(ctx: Ctx) -> dict:
    """What the front needs right after signing in or choosing: person, mosque (or None), choices."""
    out = {
        "user": _user(ctx),
        "mosque": _mosque(ctx) if ctx.mosque else None,
        "mosques": _choices(ctx),
        "support": ctx.support,
        "csrf": (ctx.session or {}).get("csrf", ""),
        "version": os.environ.get("SADA_VERSION", "dev"),
    }
    return out


def _choices(ctx: Ctx) -> list[dict]:
    """Mosques the person may open; the operator also sees the others, as support entries."""
    operator = bool(ctx.user.get("platform_admin"))
    own = [] if operator and auth.hosted() else auth.mosques_of(ctx.conn, ctx.user)
    out = [{**m, "access": "member"} for m in own]
    if operator:
        mine = {m["id"] for m in own}
        out += [{**m, "access": "support"} for m in auth.all_mosques(ctx.conn) if m["id"] not in mine]
    return out


@router.get("/mosques")
def mosques(ctx: PreCtxDep):
    return {"mosques": _choices(ctx), "current": (ctx.session or {}).get("mosque_id")}


@router.post("/mosques/select")
async def select_mosque(ctx: PreCtxDep):
    body = await ctx.raw_body()
    raw = str(body.get("mosque_id", ""))
    mid = int(raw) if raw.isdigit() else 0
    kind = auth.choose_mosque(ctx.conn, ctx.session, ctx.user, mid)
    if not kind:
        db.audit(ctx.conn, None, ctx.user["id"], "mosque.refusee", ip=ctx.ip, mosque=mid)
        raise Forbidden()
    db.audit(ctx.conn, mid, ctx.user["id"], "support.entree" if kind == "support" else "mosque.ouverte", ip=ctx.ip)
    ctx.session = {**ctx.session, "mosque_id": mid, "support": 1 if kind == "support" else 0}
    ctx.support = kind == "support"
    ctx.mosque = open_mosque(ctx.conn, ctx.session, ctx.user)
    counts = {"pending": count_clips(ctx, "a_valider"), "failed": clip_problems(ctx), "tarawih": _tarawih_nights(ctx)}
    return {**_state(ctx), "counts": counts}


@router.post("/mosques/leave")
def leave_mosque(ctx: PreCtxDep):
    ctx.need_csrf()
    mid = (ctx.session or {}).get("mosque_id")
    if mid:
        db.audit(ctx.conn, mid, ctx.user["id"], "support.sortie" if ctx.support else "mosque.fermee", ip=ctx.ip)
    auth.leave_mosque(ctx.conn, ctx.session)
    ctx.session = {**ctx.session, "mosque_id": None, "support": 0}
    ctx.support, ctx.mosque = False, None
    return _state(ctx)


@router.post("/logout")
def logout(ctx: PreCtxDep):
    ctx.need_csrf()
    auth.close_session(ctx.conn, ctx.request.cookies.get(auth.COOKIE))
    db.audit(ctx.conn, (ctx.session or {}).get("mosque_id"), ctx.user["id"], "deconnexion", ip=ctx.ip)
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@router.get("/session")
def session(request: Request, conn: ConnDep):
    """Who is logged in, on which mosque (if any), and what they are allowed to do.

    Also serves as a probe: without a session, the answer is 401.
    """
    ctx = get_pre_ctx(request, conn)
    ctx.mosque = open_mosque(conn, ctx.session, ctx.user)
    ctx.support = bool(ctx.session.get("support")) and ctx.mosque is not None
    # The number of clips to validate comes with the session: the navigation shows it as a badge
    # without a second request at startup.
    pending = count_clips(ctx, "a_valider") if ctx.mosque else 0
    counts = {"pending": pending, "failed": clip_problems(ctx) if ctx.mosque else 0, "tarawih": _tarawih_nights(ctx)}
    return {**_state(ctx), "counts": counts}
