"""Publication history, connector status and notification settings."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import dirs as dirs_mod
from .. import orm, publish
from ..context import CtxDep
from ..models import Clip, Khutba, Publication, User
from .common import _a_table, _notification_settings

router = APIRouter()


@router.get("/publications")
def publications(ctx: CtxDep):
    """Real connector status (availability and reason), ready clips and history; notification settings for admins only."""
    ctx.require("publish")
    ready = orm.rows(
        ctx.conn,
        select(Clip.__table__, Khutba.label.label("sermon"), Khutba.date)
        .join(Khutba, Khutba.id == Clip.khutba_id)
        .where(Clip.mosque_id == ctx.mosque["id"], Clip.status == "valide")
        .order_by(Clip.decided_at.desc()),
    )
    pubs = (
        orm.rows(
            ctx.conn,
            select(
                Publication.__table__,
                User.display_name.label("who"),
                Clip.idx,
                Clip.kind,
                Khutba.label.label("sermon"),
                Khutba.date,
            )
            .join(Clip, Clip.id == Publication.clip_id)
            .join(Khutba, Khutba.id == Clip.khutba_id)
            .outerjoin(User, User.id == Publication.user_id)
            .where(Publication.mosque_id == ctx.mosque["id"])
            .order_by(Publication.id.desc())
            .limit(100),
        )
        if _a_table(ctx.conn, "publications")
        else []
    )
    for p in pubs:  # never expose the machine's real path in the UI
        p["location"] = dirs_mod.label(p["location"]) if p.get("location") else ""
    rows = publish.connector_rows(ctx.conn, ctx.mosque)
    for r in rows:  # the screen says when a connector cannot publish for real
        r["implemented"] = publish.CONNECTORS[r["key"]].implemented
    return {
        "connectors": rows,
        "ready": ready,
        "publications": pubs,
        "can_configure": ctx.can("admin"),
        "notifications": _notification_settings(ctx) if ctx.can("admin") else None,
    }
