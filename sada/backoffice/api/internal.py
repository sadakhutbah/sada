"""Machine-to-machine endpoints: no session, one local token (`X-Internal-Token`).

Only the orchestrator calls them, from the same machine. They never go through the browser: the
CSP, the CSRF and the permissions of the JSON API do not apply here.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...i18n import _
from .. import db, destinations, gabarit, internal, live_input, storage
from .. import screens as screens_mod
from .. import variables_ecran as variables_mod
from ..context import ConnDep

router = APIRouter()


def _refuse() -> JSONResponse:
    return JSONResponse({"error": _("internal token required")}, status_code=403)


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
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
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
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
    return {"mosque_id": mid, "urls": destinations.effective(conn, mid)}


@router.get("/internal/storage")
def storage_target(request: Request, conn: ConnDep, port: int = 0):
    """Bucket, keys and folder the engine needs to send the replays; null when nothing is configured."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
    return {"mosque_id": mid, "target": storage.effective(conn, mid)}


@router.get("/internal/screens")
def screen_texts(request: Request, conn: ConnDep, port: int = 0):
    """Texts the mosque changed on the live screens: {écran: {champ: texte}}; empty = defaults."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
    mosque = db.one(conn, "SELECT * FROM mosques WHERE id = ?", mid)
    if not mosque:
        return JSONResponse({"error": _("mosque not found")}, status_code=404)
    return {"mosque_id": mid, "screens": screens_mod.saved(conn, mid),
            "variables": variables_mod.variables(conn, mosque)}


@router.get("/internal/gabarit")
def gabarit_page(request: Request, conn: ConnDep, port: int = 0):
    """Le fichier d'écrans importé par la mosquée, tel qu'il doit s'afficher à l'antenne.

    La page est servie décorée (l'écran voulu est rendu visible) et les textes de la mosquée y sont
    posés, exactement comme pour l'aperçu : c'est le même document, donc la même image.
    ``variables`` porte ce que le moteur seul connaît (logo, nom, textes arabes saisis).
    """
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
    mosquee = db.one(conn, "SELECT * FROM mosques WHERE id = ?", mid) or {}
    fichier = gabarit.lire(mosquee)
    if not fichier:
        return {"mosque_id": mid, "nom": "", "present": False, "pages": {}, "variables": {}}
    valeurs = variables_mod.variables(conn, mosquee)
    # une page par écran, chacune avec l'écran voulu visible : le moteur n'a pas à découper le HTML
    pages = {e: gabarit.pour_ecran(fichier, e) for e in gabarit.ECRANS}
    return {"mosque_id": mid, "nom": mosquee.get("name") or "", "present": True,
            "pages": pages, "variables": valeurs}


@router.get("/internal/live-input")
def live_input_url(request: Request, conn: ConnDep, port: int = 0):
    """Address the encoder must read; empty when nothing was configured in the interface."""
    if not internal.check(request.headers.get(internal.HEADER)):
        return _refuse()
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
    return {"mosque_id": mid, "url": live_input.effective(conn, mid)}
