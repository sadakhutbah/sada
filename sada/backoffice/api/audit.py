"""Journal: the mosque's audit trail, paginated and filterable."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db
from ..context import CtxDep

router = APIRouter()


@router.get("/audit")
def audit(ctx: CtxDep, action: str = "", limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Journal, page by page: a mosque's journal does not fit on one screen."""
    ctx.require("journal")
    filtre = "(mosque_id = ? AND (? = '' OR action LIKE ? || '%'))"
    rows = db.all_(
        ctx.conn,
        "SELECT a.*, u.display_name who FROM audit a LEFT JOIN users u ON u.id = a.user_id "
        "WHERE a.mosque_id = ? AND (? = '' OR a.action LIKE ? || '%') "
        "ORDER BY a.id DESC LIMIT ? OFFSET ?",
        ctx.mosque["id"],
        action,
        action,
        limit,
        offset,
    )
    total = (
        db.one(ctx.conn, f"SELECT COUNT(*) n FROM audit WHERE {filtre}", ctx.mosque["id"], action, action) or {"n": 0}
    )["n"]
    # Action keys really present for this mosque, for the filter. Labels come from the front end.
    actions = [
        r["action"]
        for r in db.all_(
            ctx.conn, "SELECT DISTINCT action FROM audit WHERE mosque_id = ? ORDER BY action", ctx.mosque["id"]
        )
    ]
    return {"items": rows, "count": len(rows), "total": total, "offset": offset, "limit": limit, "actions": actions}
