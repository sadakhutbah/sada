"""Client de l'API de l'orchestrateur (minbar/api.py) : statut du live et commandes manuelles.

Mode A : l'orchestrateur tourne sur la même machine (http://127.0.0.1:8088). Mode B : URL + jeton par mosquée
(tunnel/VPN vers la machine de la mosquée) ; le code ne change pas.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .. import runs
from ..state import STATE_LABELS

@dataclass(frozen=True)
class Command:
    label: str
    style: str                       # classe du bouton
    confirm: str = ""                # question posée avant l'envoi
    params: tuple = ()               # paramètres fixes de la requête
    param: str = ""                  # name of the variable parameter ("min", "name"), if the command has one
    choices: tuple = ()              # allowed parameter values: ((value, label), ...)


# Scenes that can be picked by hand (janaza has its own command, which opens its form).
# Labels are only used for the audit trail; the front end translates by value.
SCENES = (("avant", "Before"), ("khutba", "Khutba"), ("priere", "Prayer"), ("entracte", "Intermission"),
          ("termine", "Finished"), ("dars", "Dars"), ("dars_fin", "Dars end"))

# Orchestrator commands (minbar.state.Machine.COMMANDS) offered on the dashboard, in display order.
COMMANDS = {
    "start": Command("Start", "leaf", "start"),
    "pause": Command("Pause", "light"),
    "resume": Command("Resume", "light"),
    "auto": Command("Automatic", "light"),
    "extend": Command("Extend", "leaf", param="min", choices=(("5", "+5 min"), ("15", "+15 min"))),
    "skip": Command("Skip session", "light", "skip"),
    "scene": Command("Change scene", "light", param="name", choices=SCENES),
    "janaza": Command("Janaza", "light", "janaza"),
    "stop": Command("Arrêter", "danger", "Arrêter le live maintenant ?"),
    "cancel": Command("Annuler aujourd'hui", "light",
                      "Annuler tout le live d'aujourd'hui ? Les sessions à venir ne seront pas diffusées."),
}


def choice_label(cmd: str, valeur: str | None) -> str:
    """Libellé de la variante choisie (« +15 min »), ou celui de la commande si elle n'a pas de choix."""
    c = COMMANDS[cmd]
    return dict(c.choices).get(valeur, c.label) if valeur else c.label


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


def status(mosque: dict, timeout: float = 3.0) -> dict:
    return _call(mosque, "GET", "status", timeout=timeout)


# --- Statut mis en cache
#
# Un orchestrateur éteint ne répond pas : chaque lecture coûte plusieurs secondes d'attente. Le
# tableau de bord le lisait à chaque affichage, d'où l'impression qu'il ne se chargeait jamais.
# On garde donc un instantané: la première lecture attend un peu, les suivantes sont immédiates et
# déclenchent un rafraîchissement en arrière-plan. Une panne se voit au plus un cycle plus tard,
# ce qui suffit pour un indicateur.
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
    """Statut de l'orchestrateur, rafraîchi au plus une fois toutes les `ttl` secondes.

    `forcer` : lecture immédiate et bloquante (après une commande, on veut la conséquence réelle)."""
    cle = _cle(mosque)
    entree = None
    if not forcer:
        with _verrou:
            entree = _caches.get(cle)
            if entree and (time.monotonic() - entree[0] < ttl or cle in _en_cours):
                return entree[1]
            if entree is not None:
                _en_cours.add(cle)
        if entree is not None:                  # périmé : on sert l'instantané, on rafraîchit derrière
            threading.Thread(target=_rafraichir, args=(mosque, cle), daemon=True,
                             name="statut-live").start()
            return entree[1]
    resultat = _lire(mosque)                    # premier appel (ou lecture forcée) : on attend
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


def command(mosque: dict, cmd: str, arg: str | None = None, info: str | None = None,
            valeur: str | None = None) -> dict:
    """arg : nom du défunt (janaza) ; info : mention de la fiche janaza ; valeur : choix d'une commande
    à paramètre (minutes de extend, nom de la scène de scene).

    Une valeur hors de la liste permise est refusée ici, avant tout appel réseau. Une commande ne
    passe jamais par le cache du statut : elle part tout de suite."""
    if cmd not in COMMANDS:
        raise OrchestratorError("commande inconnue")
    c = COMMANDS[cmd]
    params = dict(c.params)
    if c.choices:
        permises = [v for v, _ in c.choices]
        valeur = valeur or (permises[0] if cmd == "extend" else None)    # extend : +5 min par défaut
        if valeur not in permises:
            raise ValueError(f"valeur invalide pour « {c.label} »")
        params[c.param] = valeur
    if cmd == "janaza":
        if arg:
            params["name"] = arg[:80]
        if info and arg:                # une mention seule n'a pas de sens sans nom
            params["info"] = info[:60]
    return _call(mosque, "POST", cmd, params or None)


def summarize(st: dict) -> dict:
    """Statut brut de l'orchestrateur -> données d'affichage."""
    sessions = st.get("sessions") or []
    live = next((s for s in sessions if s.get("status") == "live"), None)
    planned = [s for s in sessions if runs.produces_content(s)]
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
        "janaza_name": st.get("janaza_name"), "janaza_info": st.get("janaza_info"),
        "encoder_alive": bool((st.get("encoder") or {}).get("alive")),
        "vad_connected": bool((st.get("vad") or {}).get("connected")),
        "sessions": [{"planned": s.get("planned"), "status": SESSION_LABELS.get(s.get("status"), s.get("status")),
                      "start": s.get("start_clock"), "end": s.get("end_clock")} for s in sessions],
    }
