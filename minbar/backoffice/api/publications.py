"""Publication history, connector status and notification settings."""
from __future__ import annotations

from fastapi import APIRouter

from .. import db, dirs as dirs_mod, publish
from ..context import CtxDep
from .common import _a_table, _notification_settings

router = APIRouter()


@router.get("/publications")
def publications(ctx: CtxDep):
    """Real connector status (availability and reason), ready clips and history; notification settings for admins only."""
    ctx.require("publish")
    ready = db.all_(ctx.conn, "SELECT c.*, k.label sermon, k.date FROM clips c JOIN khutbas k ON k.id = c.khutba_id "
                              "WHERE c.mosque_id = ? AND c.status = 'valide' ORDER BY c.decided_at DESC", ctx.mosque["id"])
    pubs = db.all_(ctx.conn, "SELECT p.*, u.display_name who, c.idx, c.kind, k.label sermon, k.date FROM publications p "
                             "JOIN clips c ON c.id = p.clip_id JOIN khutbas k ON k.id = c.khutba_id "
                             "LEFT JOIN users u ON u.id = p.user_id WHERE p.mosque_id = ? ORDER BY p.id DESC LIMIT 100",
                   ctx.mosque["id"]) if _a_table(ctx.conn, "publications") else []
    for p in pubs:                                       # never expose the machine's real path in the UI
        p["location"] = dirs_mod.label(p["location"]) if p.get("location") else ""
    rows = publish.connector_rows(ctx.conn, ctx.mosque)
    for r in rows:                                       # the screen says when a connector cannot publish for real
        r["implemented"] = publish.CONNECTORS[r["key"]].implemented
    return {"connectors": rows, "ready": ready, "publications": pubs,
            "can_configure": ctx.can("admin"),
            "notifications": _notification_settings(ctx) if ctx.can("admin") else None}
