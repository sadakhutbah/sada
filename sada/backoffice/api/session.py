"""Login, logout and the session probe."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .. import auth, db
from ..context import PRE_COOKIE, ConnDep, Ctx, CtxDep, _sec, imam_filter
from .common import _mosque, _user

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
    ip = request.client.host if request.client else "?"
    if not pre or not secrets.compare_digest(pre, str(f.get("pre", ""))):
        return JSONResponse({"error": "challenge"}, status_code=403)
    lim = request.app.state.limiter
    if lim.blocked(ip, username):
        return JSONResponse({"error": "rate"}, status_code=429)
    u = await run_in_threadpool(auth.authenticate, conn, username, password)
    if not u:
        lim.fail(ip, username)
        connu = db.one(conn, "SELECT mosque_id FROM users WHERE username = ?", username)
        db.audit(conn, connu["mosque_id"] if connu else None, None, "connexion.echec", ip=ip, identifiant=username[:64])
        return JSONResponse({"error": "identifiants"}, status_code=401)
    lim.success(ip, username)
    token = auth.open_session(conn, u["id"], ip, request.headers.get("user-agent"))
    db.audit(conn, u["mosque_id"], u["id"], "connexion", ip=ip)
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", u["mosque_id"])
    sess = auth.load_session(conn, token, request.headers.get("user-agent"), ip)[0]
    ctx = Ctx(request, conn, sess, u, m)
    r = JSONResponse({"user": _user(ctx), "mosque": _mosque(ctx), "csrf": sess["csrf"]})
    r.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=_secure(request),
                 max_age=auth.SESSION_TTL, path="/")
    r.delete_cookie(PRE_COOKIE)
    return r


@router.post("/logout")
def logout(ctx: CtxDep):
    ctx.need_csrf()
    auth.close_session(ctx.conn, ctx.request.cookies.get(auth.COOKIE))
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "deconnexion", ip=ctx.ip)
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@router.get("/session")
def session(ctx: CtxDep):
    """Who is logged in, on which mosque, and what they are allowed to do.

    Also serves as a probe: without a session, the authentication interceptor answers 401.
    """
    # The number of clips to validate comes with the session: the navigation shows it as a badge
    # without a second request at startup.
    where, args = imam_filter(ctx)
    a_valider = db.one(ctx.conn, f"SELECT COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                                 f"WHERE {where} AND c.status = 'a_valider'", *args)["n"]
    return {"user": _user(ctx), "mosque": _mosque(ctx), "csrf": (ctx.session or {}).get("csrf", ""),
            "counts": {"pending": a_valider}}
