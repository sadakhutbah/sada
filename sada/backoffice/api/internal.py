"""Machine-to-machine endpoints: no session, a token in `X-Internal-Token`.

Only orchestrators call them: each with its mosque's agent token, or with the shared local token
while the instance has a single mosque. They never go through the browser: the
CSP, the CSRF and the permissions of the JSON API do not apply here.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from ...i18n import _
from .. import (
    branding,
    destinations,
    events_store,
    gabarit,
    internal,
    live_input,
    live_watch,
    mawaqit_store,
    orm,
    storage,
)
from .. import screens as screens_mod
from .. import variables_ecran as variables_mod
from ..context import ConnDep
from ..models import Mosque, Notification

router = APIRouter()


def _refuse() -> JSONResponse:
    return JSONResponse({"error": _("internal token required")}, status_code=403)


def _mosque_id(conn, port: int) -> int | None:
    """The mosque whose orchestrator listens on `port`, or the only mosque."""
    mosques = orm.rows(conn, select(Mosque.id, Mosque.orchestrator_url))
    ids = [m["id"] for m in mosques if port and urlparse(m["orchestrator_url"] or "").port == port]
    if not ids and len(mosques) == 1:
        ids = [mosques[0]["id"]]
    return ids[0] if len(ids) == 1 else None


def _caller(request: Request, conn, port: int) -> int | JSONResponse:
    """Mosque of the calling orchestrator.

    A mosque's own agent token names it: the port is then ignored, and that token opens nothing
    of another mosque. The shared local token (one machine, both processes) only holds while the
    instance has a single mosque: with several, it would let any orchestrator read every mosque.
    """
    given = request.headers.get(internal.HEADER)
    if not given:
        return _refuse()
    mid = internal.agent_mosque(conn, given)
    if mid is not None:
        return mid
    if not internal.check(given):
        return _refuse()
    if conn.scalar(select(func.count()).select_from(Mosque)) > 1:
        return JSONResponse({"error": _("several mosques: a per-mosque agent token is required")}, status_code=403)
    mid = _mosque_id(conn, port)
    if mid is None:
        return JSONResponse({"error": _("cannot determine the mosque")}, status_code=404)
    return mid


@router.get("/internal/notification-settings")
def notification_settings(request: Request, conn: ConnDep, port: int = 0):
    """Notification settings of the mosque whose orchestrator listens on `port` (or of the only mosque)."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    r = orm.first(conn, Notification, mosque_id=mid) or {}
    return {
        "mosque_id": mid,
        "mode": r.get("mode", ""),
        "webhook": r.get("webhook", ""),
        "telegram_token": r.get("telegram_token", ""),
        "telegram_chat": r.get("telegram_chat", ""),
    }


@router.get("/internal/live-targets")
def live_targets(request: Request, conn: ConnDep, port: int = 0):
    """Enabled live destinations, keys in clear: the encoder needs the whole address."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    return {"mosque_id": mid, "urls": destinations.effective(conn, mid)}


@router.get("/internal/storage")
def storage_target(request: Request, conn: ConnDep, port: int = 0):
    """Bucket, keys and folder the engine needs to send the replays; null when nothing is configured."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    return {"mosque_id": mid, "target": storage.effective(conn, mid)}


@router.get("/internal/screens")
def screen_texts(request: Request, conn: ConnDep, port: int = 0):
    """Texts the mosque changed on the live screens: {écran: {champ: texte}}; empty = defaults."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    mosque = orm.first(conn, Mosque, id=mid)
    if not mosque:
        return JSONResponse({"error": _("mosque not found")}, status_code=404)
    return {
        "mosque_id": mid,
        "screens": screens_mod.saved(conn, mid),
        "variables": variables_mod.variables(conn, mosque),
    }


@router.get("/internal/events")
def mosque_events(request: Request, conn: ConnDep, port: int = 0):
    """Programme of the calling orchestrator's mosque (dars, conferences), as raw entries."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    return {"mosque_id": mid, "events": events_store.read(conn, mid)}


@router.get("/internal/times")
def mosque_times(request: Request, conn: ConnDep, port: int = 0):
    """Mawaqit times of the calling orchestrator's mosque, through the per-mosque cache."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    mosque = orm.first(conn, Mosque, id=mid)
    try:
        return {"mosque_id": mid, "times": mawaqit_store.times(conn, mosque)}
    except Exception as e:  # noqa: BLE001 — no source at all: the orchestrator falls back locally
        return JSONResponse({"error": str(e)}, status_code=503)


@router.post("/internal/publish-auth")
async def publish_auth(request: Request, conn: ConnDep):
    """Asked by the stream receiver (MediaMTX `authHTTPAddress`) for every publish or read.

    Publishing `live/<slug>` needs the mosque's stream key as password. Reading is only for the
    orchestrators and the back office (control room monitors, proxied behind the session), inside
    the private network: the receiver's read ports are never published.
    """
    import ipaddress

    try:
        q = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({}, status_code=400)
    action, path = q.get("action"), str(q.get("path") or "")
    slug = path.removeprefix("live/") if path.startswith("live/") else ""
    if action == "publish" and slug.endswith("-prog"):
        # programme copy sent by an orchestrator (monitoring), private network only
        try:
            ok = ipaddress.ip_address(str(q.get("ip") or "")).is_private
        except ValueError:
            ok = False
    elif action == "publish":
        ok = internal.publish_allowed(conn, slug, q.get("password"))
    elif action == "api":
        # stream health for the control room: the back office only, with its derived credentials
        try:
            ok = ipaddress.ip_address(str(q.get("ip") or "")).is_private
        except ValueError:
            ok = False
        ok = ok and live_watch.api_allowed(q.get("user"), q.get("password"))
    elif action in ("read", "playback"):
        try:
            ok = bool(slug) and ipaddress.ip_address(str(q.get("ip") or "")).is_private
        except ValueError:
            ok = False
    else:
        ok = False
    return JSONResponse({}, status_code=200 if ok else 401)


@router.get("/internal/gabarit")
def gabarit_page(request: Request, conn: ConnDep, port: int = 0):
    """Le fichier d'écrans importé par la mosquée, tel qu'il doit s'afficher à l'antenne.

    La page est servie décorée (l'écran voulu est rendu visible) et les textes de la mosquée y sont
    posés, exactement comme pour l'aperçu : c'est le même document, donc la même image.
    ``variables`` porte ce que le moteur seul connaît (logo, nom, textes arabes saisis).
    """
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    mosquee = orm.first(conn, Mosque, id=mid) or {}
    fichier = gabarit.lire(mosquee)
    if not fichier:
        return {"mosque_id": mid, "nom": "", "present": False, "pages": {}, "variables": {}}
    valeurs = variables_mod.variables(conn, mosquee)
    # The engine renders the page from a local file: a server-relative logo address would not load.
    # The logo is therefore embedded in the page itself.
    if valeurs.get("logo"):
        valeurs["logo"] = branding.data_url(mosquee)
    # une page par écran (plein ou bandeau), chacune avec l'écran voulu visible : le moteur n'a pas
    # à découper le HTML. Un écran absent du fichier donne une page vide, et le moteur dessine alors.
    pages = {e: gabarit.pour_ecran(fichier, e) for e in gabarit.CONNUS}
    return {"mosque_id": mid, "nom": mosquee.get("name") or "", "present": True, "pages": pages, "variables": valeurs}


@router.get("/internal/live-input")
def live_input_url(request: Request, conn: ConnDep, port: int = 0):
    """Address the encoder must read; empty when nothing was configured in the interface."""
    mid = _caller(request, conn, port)
    if isinstance(mid, JSONResponse):
        return mid
    return {"mosque_id": mid, "url": live_input.effective(conn, mid)}
