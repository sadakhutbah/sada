"""Client de l'API de l'orchestrateur (minbar/api.py) : statut du live et commandes manuelles.

Mode A : l'orchestrateur tourne sur la même machine (http://127.0.0.1:8088). Mode B : URL + jeton par mosquée
(tunnel/VPN vers la machine de la mosquée) ; le code ne change pas.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ..state import State

# commandes de l'orchestrateur exposées dans le back-office -> libellé (boutons, journal)
COMMANDS = {"extend": "+5 min", "janaza": "Janaza", "stop": "Arrêter", "cancel": "Annuler aujourd'hui"}

STATE_LABELS = {
    State.IDLE: "En veille", State.ARME: "Armé (fenêtre ouverte)", State.AVANT: "Début imminent",
    State.KHUTBA: "Khutba en direct", State.PRIERE: "Prière en cours", State.ENTRACTE: "Entracte",
    State.TERMINE: "Terminé", State.JANAZA: "Salat al-Janaza", State.FIN: "Live terminé",
}
SESSION_LABELS = {"pending": "à venir", "live": "en direct", "done": "terminée", "skipped": "sautée", "janaza": "janaza"}


class OrchestratorError(Exception):
    pass


def _call(mosque: dict, method: str, path: str, params: dict | None = None, timeout: float = 3.0) -> dict:
    base = (mosque.get("orchestrator_url") or "").rstrip("/")
    if not base:
        raise OrchestratorError("adresse de l'orchestrateur non configurée")
    url = f"{base}/{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if mosque.get("orchestrator_token"):
        req.add_header("X-Token", mosque["orchestrator_token"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise OrchestratorError(f"orchestrateur : erreur HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OrchestratorError("orchestrateur injoignable (le live n'est pas lancé sur cette machine ?)") from e


def status(mosque: dict) -> dict:
    return _call(mosque, "GET", "status")


def command(mosque: dict, cmd: str, arg: str | None = None) -> dict:
    if cmd not in COMMANDS:
        raise OrchestratorError("commande inconnue")
    params = {}
    if cmd == "extend":
        params["min"] = "5"
    elif cmd == "janaza" and arg:
        params["name"] = arg[:80]
    return _call(mosque, "POST", cmd, params or None)


def summarize(st: dict) -> dict:
    """Statut brut de l'orchestrateur -> données d'affichage."""
    sessions = st.get("sessions") or []
    live = next((s for s in sessions if s.get("status") == "live"), None)
    planned = [s for s in sessions if s.get("status") != "janaza"]
    cur = live or next((s for s in planned if s.get("status") == "pending"), None)
    if cur is not None:
        n = planned.index(cur) + 1 if cur in planned else None
        session = f"Session {n}/{len(planned)} — {cur['planned']}" if n else cur.get("planned")
    else:
        session = "aucune session à venir" if planned else "—"
    return {
        "state": st.get("state"), "state_label": STATE_LABELS.get(st.get("state"), st.get("state")),
        "clock": st.get("clock"), "session": session, "next_session": st.get("next_session"),
        "stream_on": bool(st.get("stream_on")), "mic": bool(st.get("mic")), "override": bool(st.get("override")),
        "last_reason": st.get("last_reason") or "", "run_id": st.get("run_id"),
        "encoder_alive": bool((st.get("encoder") or {}).get("alive")),
        "vad_connected": bool((st.get("vad") or {}).get("connected")),
        "sessions": [{"planned": s.get("planned"), "status": SESSION_LABELS.get(s.get("status"), s.get("status")),
                      "start": s.get("start_clock"), "end": s.get("end_clock")} for s in sessions],
    }
