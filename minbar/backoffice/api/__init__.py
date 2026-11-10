"""Versioned JSON API (`/api/v1`): everything the React front does.

Reads and writes go through here; the access rules are the back-office's (Ctx.require),
not a second matrix. Writes require the session token in the `X-CSRF` header.

One router per domain: this module only assembles them and exposes the shared request context
(`minbar.backoffice.context`) and the shared response shapes (`common`).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..context import ConnDep, Ctx, CtxDep, PRE_COOKIE
from .common import PERMISSIONS
from . import agenda, audit, clips, dashboard, internal, publications, session, settings, sermons, users

__all__ = ["router", "Ctx", "CtxDep", "ConnDep", "PRE_COOKIE", "PERMISSIONS"]

router = APIRouter(prefix="/api/v1", tags=["api"], include_in_schema=False)

for _module in (session, sermons, clips, dashboard, publications, settings, audit, users, agenda, internal):
    router.include_router(_module.router)


@router.get("/health")
def health():
    return JSONResponse({"ok": True})
