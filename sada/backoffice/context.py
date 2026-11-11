"""Request context and access rules shared by the application and the JSON API.

Kept apart from `app.py` so that the API modules depend on this module, never on the application:
`app.py` imports the API router, the API never imports `app.py`.
"""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, Request

from . import auth, db, live

PRE_COOKIE = "sada_pre"


# ------------------------------------------------------------------ errors

class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    """Readable request refused by a business rule (empty field, unknown value...)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ context

class Ctx:
    def __init__(self, request: Request, conn, session: dict, user: dict, mosque: dict):
        self.request, self.conn, self.session, self.user, self.mosque = request, conn, session, user, mosque
        self.ip = request.client.host if request.client else None

    def can(self, perm: str) -> bool:
        return auth.can(self.user, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            db.audit(self.conn, self.mosque["id"], self.user["id"], "acces.refuse", ip=self.ip,
                     chemin=self.request.url.path, droit=perm)
            raise Forbidden()

    def csrf_ok(self) -> bool:
        """Anti-replay token for writes: `X-CSRF` header (the front), or `csrf` parameter."""
        jeton = self.request.headers.get("x-csrf") or self.request.query_params.get("csrf")
        return auth.check_csrf(self.session, jeton)

    def need_csrf(self) -> None:
        if not self.csrf_ok():
            raise Forbidden()

    async def body(self) -> dict:
        """Request body as text values, after the CSRF token check.

        JSON for the front; a bare form remains accepted for a client without a header.
        Uploaded files (logo) are kept in `self.files`.
        """
        corps = await self.raw_body()
        return {k: (v if isinstance(v, str) else "") for k, v in corps.items()}

    async def raw_body(self) -> dict:
        """Request body as sent (a list of objects stays a list), after the CSRF token check."""
        self.need_csrf()
        self.files: dict = {}
        ctype = (self.request.headers.get("content-type") or "").lower()
        try:
            if ctype.startswith("application/json"):
                corps = await self.request.json()
            else:
                form = await self.request.form()
                self.files = {k: v for k, v in form.items() if not isinstance(v, str)}
                corps = dict(form)
        except Exception:                       # noqa: BLE001 — empty or unreadable body
            corps = {}
        return corps if isinstance(corps, dict) else {}


def get_conn(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _sec(request) -> bool:
    """Request served over HTTPS (directly or through a proxy that sets X-Forwarded-Proto)."""
    fwd = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return request.url.scheme == "https" or fwd == "https"


ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]


def get_ctx(request: Request, conn: ConnDep) -> Ctx:
    res = auth.load_session(conn, request.cookies.get(auth.COOKIE), request.headers.get("user-agent"),
                            request.client.host if request.client else None)
    if not res:
        raise LoginRequired()
    s, u = res
    m = db.one(conn, "SELECT * FROM mosques WHERE id = ?", u["mosque_id"])
    if not m:
        raise LoginRequired()
    return Ctx(request, conn, s, u, m)


CtxDep = Annotated[Ctx, Depends(get_ctx)]


# ------------------------------------------------------------------ shared rules

def imam_filter(ctx: Ctx) -> tuple[str, tuple]:
    """Visible khutbas (alias k): those of the mosque; for an imam, only his own."""
    if ctx.can("history_all"):
        return "k.mosque_id = ?", (ctx.mosque["id"],)
    return "k.mosque_id = ? AND k.imam_id = ?", (ctx.mosque["id"], ctx.user["id"])


def count_clips(ctx: Ctx, status: str) -> int:
    where, args = imam_filter(ctx)
    return db.one(ctx.conn, f"SELECT COUNT(*) n FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                            f"WHERE {where} AND c.status = ?", *args, status)["n"]


def khutba_or_404(ctx: Ctx, kid: int) -> dict:
    where, args = imam_filter(ctx)
    k = db.one(ctx.conn, f"SELECT k.* FROM khutbas k WHERE k.id = ? AND {where}", kid, *args)
    if not k:
        raise NotFound()
    return k


def clip_or_404(ctx: Ctx, cid: int) -> tuple[dict, dict]:
    c = db.one(ctx.conn, "SELECT * FROM clips WHERE id = ? AND mosque_id = ?", cid, ctx.mosque["id"])
    if not c:
        raise NotFound()
    return c, khutba_or_404(ctx, c["khutba_id"])


def live_status(ctx: Ctx, forcer: bool = False) -> dict:
    """Live status for display, served from a snapshot: a powered-off orchestrator must not
    make each page wait several seconds."""
    return live.status_cached(ctx.mosque, forcer=forcer)
