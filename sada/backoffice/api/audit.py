"""Journal: the mosque's audit trail, paginated and filterable."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .. import orm
from ..context import CtxDep
from ..models import Audit, User

router = APIRouter()


@router.get("/audit")
def audit(ctx: CtxDep, action: str = "", limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Journal, page by page: a mosque's journal does not fit on one screen."""
    ctx.require("journal")
    where = [Audit.mosque_id == ctx.mosque["id"]]
    if action:
        where.append(Audit.action.startswith(action, autoescape=True))
    rows = orm.rows(
        ctx.conn,
        select(Audit.__table__, User.display_name.label("who"))
        .outerjoin(User, User.id == Audit.user_id)
        .where(*where)
        .order_by(Audit.id.desc())
        .limit(limit)
        .offset(offset),
    )
    total = ctx.conn.scalar(select(func.count()).select_from(Audit).where(*where)) or 0
    # Action keys really present for this mosque, for the filter. Labels come from the front end.
    actions = list(
        ctx.conn.scalars(
            select(Audit.action).distinct().where(Audit.mosque_id == ctx.mosque["id"]).order_by(Audit.action)
        )
    )
    return {"items": rows, "count": len(rows), "total": total, "offset": offset, "limit": limit, "actions": actions}
