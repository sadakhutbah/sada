"""Machine-to-machine endpoints: no session, one local token (`X-Internal-Token`).

Only the orchestrator calls them, from the same machine. They never go through the browser: the
CSP, the CSRF and the permissions of the JSON API do not apply here.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import db, internal
from ..context import ConnDep

router = APIRouter()


def _refuse() -> JSONResponse:
    return JSONResponse({"error": "jeton interne requis"}, status_code=403)


@router.get("/internal/notification-settings")
def notification_settings(request: Request, conn: ConnDep, port: int = 0):
    """Notification settings of the mosque whose orchestrator listens on `port` (or of the only mosque)."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mosques = db.all_(conn, "SELECT id, orchestrator_url FROM mosques")
    ids = [m["id"] for m in mosques if port and urlparse(m["orchestrator_url"] or "").port == port]
    if not ids and len(mosques) == 1:
        ids = [mosques[0]["id"]]
    if len(ids) != 1:
        return JSONResponse({"error": "mosquée indéterminée"}, status_code=404)
    r = db.one(conn, "SELECT * FROM notifications WHERE mosque_id = ?", ids[0]) or {}
    return {"mosque_id": ids[0], "mode": r.get("mode", ""), "webhook": r.get("webhook", ""),
            "telegram_token": r.get("telegram_token", ""), "telegram_chat": r.get("telegram_chat", "")}
