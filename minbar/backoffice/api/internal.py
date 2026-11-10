"""Machine-to-machine endpoints: no session, one local token (`X-Internal-Token`).

Only the orchestrator calls them, from the same machine. They never go through the browser: the
CSP, the CSRF and the permissions of the JSON API do not apply here.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import db, destinations, internal, live_input, storage
from .. import screens as screens_mod
from ..context import ConnDep

router = APIRouter()


def _refuse() -> JSONResponse:
    return JSONResponse({"error": "jeton interne requis"}, status_code=403)


def _mosque_id(conn, port: int) -> int | None:
    """The mosque whose orchestrator listens on `port`, or the only mosque."""
    mosques = db.all_(conn, "SELECT id, orchestrator_url FROM mosques")
    ids = [m["id"] for m in mosques if port and urlparse(m["orchestrator_url"] or "").port == port]
    if not ids and len(mosques) == 1:
        ids = [mosques[0]["id"]]
    return ids[0] if len(ids) == 1 else None


@router.get("/internal/notification-settings")
def notification_settings(request: Request, conn: ConnDep, port: int = 0):
    """Notification settings of the mosque whose orchestrator listens on `port` (or of the only mosque)."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": "mosquée indéterminée"}, status_code=404)
    r = db.one(conn, "SELECT * FROM notifications WHERE mosque_id = ?", mid) or {}
    return {"mosque_id": mid, "mode": r.get("mode", ""), "webhook": r.get("webhook", ""),
            "telegram_token": r.get("telegram_token", ""), "telegram_chat": r.get("telegram_chat", "")}


@router.get("/internal/live-targets")
def live_targets(request: Request, conn: ConnDep, port: int = 0):
    """Enabled live destinations, keys in clear: the encoder needs the whole address."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": "mosquée indéterminée"}, status_code=404)
    return {"mosque_id": mid, "urls": destinations.effective(conn, mid)}


@router.get("/internal/storage")
def storage_target(request: Request, conn: ConnDep, port: int = 0):
    """Bucket, keys and folder the engine needs to send the replays; null when nothing is configured."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": "mosquée indéterminée"}, status_code=404)
    return {"mosque_id": mid, "target": storage.effective(conn, mid)}


@router.get("/internal/screens")
def screen_texts(request: Request, conn: ConnDep, port: int = 0):
    """Texts the mosque changed on the live screens: {écran: {champ: texte}}; empty = defaults."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": "mosquée indéterminée"}, status_code=404)
    return {"mosque_id": mid, "screens": screens_mod.saved(conn, mid)}


@router.get("/internal/live-input")
def live_input_url(request: Request, conn: ConnDep, port: int = 0):
    """Address the encoder must read; empty when nothing was configured in the interface."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": "mosquée indéterminée"}, status_code=404)
    return {"mosque_id": mid, "url": live_input.effective(conn, mid)}
