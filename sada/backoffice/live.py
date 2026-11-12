"""Client of the orchestrator API (sada/api.py): live status and manual commands.

Mode A: the orchestrator runs on the same machine (http://127.0.0.1:8088). Mode B: URL + token per mosque
(tunnel/VPN to the mosque's machine); the code does not change.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import runs
from ..i18n import _
from ..state import EXTEND_MINUTES, PICKABLE_SCENES, Machine

# Commands offered on the dashboard: derived from the engine, in its display order. No copy of the
# list lives here, so a command added to the machine shows up by itself.
COMMANDS = tuple(Machine.COMMANDS)

# Commands that stop or cancel the live: the front end asks for confirmation and paints them red.
DESTRUCTIVE = frozenset({"stop", "cancel"})

# Commands that take a value: parameter name on the orchestrator side, and allowed values. These are
# protocol keys, translated by the front end (dashboard.choice.<value>) — never labels.
_VARIED = {"extend": ("min", EXTEND_MINUTES), "scene": ("name", PICKABLE_SCENES)}


def choices(cmd: str) -> tuple:
    """Allowed values of a command, empty if it takes none."""
    return _VARIED.get(cmd, ("", ()))[1]


class OrchestratorError(Exception):
    pass


def _call(mosque: dict, method: str, path: str, params: dict | None = None, timeout: float = 3.0) -> dict:
    base = (mosque.get("orchestrator_url") or "").rstrip("/")
    if not base:
        raise OrchestratorError(_("orchestrator address not configured"))
    url = f"{base}/{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if mosque.get("orchestrator_token"):
        req.add_header("X-Token", mosque["orchestrator_token"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise OrchestratorError(_("orchestrator: HTTP error {code}", code=e.code)) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OrchestratorError(_("orchestrator unreachable (is the live not started on this machine?)")) from e


def status(mosque: dict, timeout: float = 3.0) -> dict:
    return _call(mosque, "GET", "status", timeout=timeout)


# --- Cached status
#
# A powered-off orchestrator does not answer: each read costs several seconds of waiting. The
# dashboard read it at every display, hence the impression that it never finished loading.
# We therefore keep a snapshot: the first read waits a little, the following ones are immediate and
# trigger a refresh in the background. An outage shows up at most one cycle later,
# which is enough for an indicator.
STATUS_TTL = 10.0
_caches: dict[str, tuple[float, dict]] = {}
_en_cours: set[str] = set()
_verrou = threading.Lock()


def _cle(mosque: dict) -> str:
    return (mosque.get("orchestrator_url") or "").rstrip("/")


def _lire(mosque: dict, timeout: float = 1.5) -> dict:
    try:
        return {"ok": True, **summarize(status(mosque, timeout=timeout))}
    except OrchestratorError as e:
        return {"ok": False, "error": str(e)}


def status_cached(mosque: dict, ttl: float = STATUS_TTL, forcer: bool = False) -> dict:
    """Orchestrator status, refreshed at most once every `ttl` seconds.

    `forcer`: immediate and blocking read (after a command, we want the real consequence)."""
    cle = _cle(mosque)
    entree = None
    if not forcer:
        with _verrou:
            entree = _caches.get(cle)
            if entree and (time.monotonic() - entree[0] < ttl or cle in _en_cours):
                return entree[1]
            if entree is not None:
                _en_cours.add(cle)
        if entree is not None:  # stale: we serve the snapshot, refresh behind
            threading.Thread(target=_rafraichir, args=(mosque, cle), daemon=True, name="statut-live").start()
            return entree[1]
    resultat = _lire(mosque)  # first call (or forced read): we wait
    with _verrou:
        _caches[cle] = (time.monotonic(), resultat)
    return resultat


def _rafraichir(mosque: dict, cle: str) -> None:
    try:
        resultat = _lire(mosque)
        with _verrou:
            _caches[cle] = (time.monotonic(), resultat)
    finally:
        with _verrou:
            _en_cours.discard(cle)


def command(mosque: dict, cmd: str, arg: str | None = None, info: str | None = None, valeur: str | None = None) -> dict:
    """arg: name of the deceased (janaza); info: note on the janaza record; valeur: choice of a
    command with a parameter (minutes of extend, scene name of scene).

    A value outside the allowed list is refused here, before any network call. A command never
    goes through the status cache: it leaves immediately."""
    if cmd not in COMMANDS:
        raise OrchestratorError(_("unknown command"))
    permises = choices(cmd)
    params = {}
    if permises:
        valeur = valeur or (permises[0] if cmd == "extend" else None)  # extend: first value by default
        if valeur not in permises:
            raise ValueError(_("invalid value for “{command}”", command=cmd))
        params[_VARIED[cmd][0]] = valeur
    if cmd == "janaza":
        if arg:
            params["name"] = arg[:80]
        if info and arg:  # a note alone makes no sense without a name
            params["info"] = info[:60]
    return _call(mosque, "POST", cmd, params or None)


def summarize(st: dict) -> dict:
    """Raw orchestrator status -> protocol data: keys and values, no interface text.

    The current session is sent structured ({n, total, planned}) so the front end composes its own
    sentence and the server never writes a French label.
    """
    sessions = st.get("sessions") or []
    live = next((s for s in sessions if s.get("status") == "live"), None)
    planned = [s for s in sessions if runs.produces_content(s)]
    cur = live or next((s for s in planned if s.get("status") == "pending"), None)
    session = None
    if cur is not None:
        n = planned.index(cur) + 1 if cur in planned else None
        session = {"n": n, "total": len(planned), "planned": cur.get("planned")}
    return {
        "state": st.get("state"),
        "clock": st.get("clock"),
        "session": session,
        "has_sessions": bool(planned),
        "next_session": st.get("next_session"),
        "stream_on": bool(st.get("stream_on")),
        "mic": bool(st.get("mic")),
        "override": bool(st.get("override")),
        "last_reason": st.get("last_reason") or "",
        "run_id": st.get("run_id"),
        "janaza_name": st.get("janaza_name"),
        "janaza_info": st.get("janaza_info"),
        "encoder_alive": bool((st.get("encoder") or {}).get("alive")),
        "vad_connected": bool((st.get("vad") or {}).get("connected")),
    }
