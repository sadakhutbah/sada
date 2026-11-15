"""Request context and access rules shared by the application and the JSON API.

Kept apart from `app.py` so that the API modules depend on this module, never on the application:
`app.py` imports the API router, the API never imports `app.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import func, select

from . import auth, db, live, orm
from .models import Clip, Khutba, Mosque

PRE_COOKIE = "sada_pre"


# ------------------------------------------------------------------ errors


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class MosqueRequired(Exception):
    """Signed in, but no mosque chosen yet (hosted mode): pick one first."""


class Invalid(Exception):
    """Readable request refused by a business rule (empty field, unknown value...)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ context


class Ctx:
    def __init__(self, request: Request, conn, session: dict, user: dict, mosque: dict | None):
        self.request, self.conn, self.session, self.user, self.mosque = request, conn, session, user, mosque
        self.support = bool((session or {}).get("support"))
        self.ip = request.client.host if request.client else None

    def can(self, perm: str) -> bool:
        # Password set by an administrator: no right at all until the person has chosen their own.
        if self.user and self.user.get("must_change_password"):
            return False
        return auth.can(self.user, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            db.audit(
                self.conn,
                self.mosque["id"],
                self.user["id"],
                "acces.refuse",
                ip=self.ip,
                chemin=self.request.url.path,
                droit=perm,
            )
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
        except Exception:  # noqa: BLE001 — empty or unreadable body
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


ConnDep = Annotated[orm.Db, Depends(get_conn)]


def open_mosque(conn, s: dict, u: dict) -> dict | None:
    """The mosque a session is open on, or None while none is chosen.

    Hosted mode never falls back to the account's home mosque: signing in opens nothing.
    """
    mid = s.get("mosque_id") or (None if auth.hosted() else u["mosque_id"])
    if mid is None:
        return None
    if not s.get("support") and mid not in {m["id"] for m in auth.mosques_of(conn, u)}:
        return None
    return orm.first(conn, Mosque, id=mid)


def get_ctx(request: Request, conn: ConnDep) -> Ctx:
    res = auth.load_session(
        conn,
        request.cookies.get(auth.COOKIE),
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )
    if not res:
        raise LoginRequired()
    s, u = res
    m = open_mosque(conn, s, u)
    if m is None:
        raise MosqueRequired()
    ctx = Ctx(request, conn, s, u, m)
    if ctx.support and request.method not in ("GET", "HEAD", "OPTIONS"):
        # The operator inside a mosque: nothing happens there without a trace.
        db.audit(conn, m["id"], u["id"], "support.action", ip=ctx.ip, methode=request.method, chemin=request.url.path)
    return ctx


def get_pre_ctx(request: Request, conn: ConnDep) -> Ctx:
    """Signed in, mosque or not: for choosing, leaving and signing out."""
    res = auth.load_session(
        conn,
        request.cookies.get(auth.COOKIE),
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )
    if not res:
        raise LoginRequired()
    return Ctx(request, conn, res[0], res[1], None)


CtxDep = Annotated[Ctx, Depends(get_ctx)]
PreCtxDep = Annotated[Ctx, Depends(get_pre_ctx)]


# ------------------------------------------------------------------ shared rules


def imam_filter(ctx: Ctx) -> list:
    """Visible khutbas: those of the mosque; for an imam, only his own (conditions on `Khutba`)."""
    if ctx.can("history_all"):
        return [Khutba.mosque_id == ctx.mosque["id"]]
    return [Khutba.mosque_id == ctx.mosque["id"], Khutba.imam_id == ctx.user["id"]]


def count_clips(ctx: Ctx, status: str) -> int:
    stmt = select(func.count()).select_from(Clip).join(Khutba, Khutba.id == Clip.khutba_id)
    return ctx.conn.scalar(stmt.where(*imam_filter(ctx), Clip.status == status)) or 0


def khutba_or_404(ctx: Ctx, kid: int) -> dict:
    k = orm.first(ctx.conn, Khutba, Khutba.id == kid, *imam_filter(ctx))
    if not k:
        raise NotFound()
    return k


def clip_or_404(ctx: Ctx, cid: int) -> tuple[dict, dict]:
    c = orm.first(ctx.conn, Clip, id=cid, mosque_id=ctx.mosque["id"])
    if not c:
        raise NotFound()
    return c, khutba_or_404(ctx, c["khutba_id"])


def live_status(ctx: Ctx, forcer: bool = False) -> dict:
    """Live status for display, served from a snapshot: a powered-off orchestrator must not
    make each page wait several seconds."""
    return live.status_cached(ctx.mosque, forcer=forcer)
