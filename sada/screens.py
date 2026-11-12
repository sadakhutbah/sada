"""Texts of the live screens (slates), one source for the engine and the back office.

The engine renders these defaults; the back office shows them pre-filled and stores only what the
mosque changed (an empty value means "keep the default"). Nothing is duplicated on the two sides.

Variables, replaced when the screen is drawn: {heure} (time of the announced session), {date}
(today, in French), {jour} (weekday of the next class), {cours} (title of the class), {titre}
(title of the mosque settings), {mosquee} (mosque name). A slot whose variable is unknown at that
moment is left empty (never "Début à "), and an unknown {braces} stays visible so a typo shows.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request

from .i18n import _

# Screens whose text block is free: key of the scene in scenes.SCENES.
SCREENS = ("avant", "priere", "entracte", "termine", "janaza", "dars_fin")

# Slot -> default text. Keys end up in the API as `screen.slot`.
DEFAULTS: dict[str, dict[str, str]] = {
    "avant": {
        "eyebrow": "Début imminent",
        "sub": "Début à {heure}",
        "foot": "Merci de patienter, la diffusion commence très bientôt.",
    },
    "priere": {
        "eyebrow": "{date}",
        "title": "Prière en cours",
        "sub": "La prière n'est pas diffusée.",
        "foot": "La diffusion reprendra pour la prochaine khutba, in sha Allah.",
    },
    "entracte": {
        "eyebrow": "Entracte",
        "title_cours": "Prochain cours à {heure}",
        "title_khutba": "Prochaine khutba à {heure}",
        "title": "Entracte",
        "foot": "Le live continue : restez connectés.",
    },
    "termine": {
        "eyebrow": "Fin de la diffusion",
        "title": "Khutba terminée",
        "sub": "Jazakoum Allahou khayran — qu'Allah vous récompense.",
        "foot": "Rendez-vous vendredi prochain, in sha Allah.",
    },
    "janaza": {
        "eyebrow": "Salat al-Janaza",
        "title": "Prière funéraire",
        "sub": "La prière n'est pas diffusée.",
        "foot": "Le direct reprendra après la prière, in sha Allah.",
    },
    "dars_fin": {
        "eyebrow": "Fin de la diffusion",
        "title": "Dars terminé",
        "sub": "Qu'Allah vous récompense.",
        "foot_cours": "Prochain cours : {jour} {heure} — {cours}",
        "foot": "Merci de votre présence.",
    },
}

MAX_LEN = 160          # longest sensible line on a 1280 px screen


def defaults() -> dict[str, dict[str, str]]:
    return {s: dict(v) for s, v in DEFAULTS.items()}


def effective(saved: dict | None) -> dict[str, dict[str, str]]:
    """Defaults, with what the mosque changed on top (empty values ignored)."""
    out = defaults()
    for screen, changed in (saved or {}).items():
        if screen not in out or not isinstance(changed, dict):
            continue
        for slot, text in changed.items():
            if slot in out[screen] and isinstance(text, str) and text.strip():
                out[screen][slot] = text.strip()
    return out


def resolve(screen: str, saved: dict | None, variables: dict[str, str]) -> dict[str, str]:
    """Slot texts of a screen for the current moment, variables replaced.

    A slot mentioning a variable that is not available *now* (a session time that is not known,
    the next class that is not planned) disappears instead of showing a half sentence.
    """
    out: dict[str, str] = {}
    for slot, template in effective(saved)[screen].items():
        texte = template
        manquant = False
        for cle in _placeholders(template):
            if cle in variables and variables[cle]:
                texte = texte.replace("{" + cle + "}", variables[cle])
            elif cle in variables:
                manquant = True
                break
        out[slot] = "" if manquant else texte
    return out


def _placeholders(template: str) -> list[str]:
    """Variables the template uses, in order of appearance."""
    found: list[str] = []
    i = 0
    while True:
        a = template.find("{", i)
        if a < 0:
            return found
        b = template.find("}", a)
        if b < 0:
            return found
        found.append(template[a + 1:b])
        i = b + 1


def variables(moment, title: str = "", mosque: str = "", heure: str = "", cours: str = "",
              jour: str = "") -> dict[str, str]:
    """Values available for the substitution; an empty value hides the slots that use it."""
    return {"heure": heure, "cours": cours, "jour": jour, "titre": title or "",
            "mosquee": mosque or "", "date": _french_date(moment)}


def _french_date(moment) -> str:
    now = getattr(moment, "now", None)
    if not isinstance(now, dt.datetime):
        return ""
    from .text import french_date

    return french_date(now)


def _http_get(url: str, token: str) -> dict:      # pragma: no cover (network)
    from .backoffice.internal import HEADER

    req = urllib.request.Request(url, headers={HEADER: token})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read(65536))


def from_backoffice(backoffice_url: str, api_port: int, fetch=None, log=None) -> dict:
    """Texts saved in the interface, or {} when nothing is set (defaults stay in force)."""
    log = log or (lambda _m: None)
    if not backoffice_url:
        return {}
    from .backoffice.internal import token
    from urllib.parse import urlencode

    url = backoffice_url.rstrip("/") + "/api/v1/internal/screens?" + urlencode({"port": api_port or 0})
    try:
        data = (fetch or _http_get)(url, token()) or {}
    except Exception as e:                        # noqa: BLE001 — the live never waits on the back office
        log(_("Screens: back-office settings unreachable ({error}) — default texts kept", error=e))
        return {}
    textes = data.get("screens") or {}
    if not isinstance(textes, dict):
        return {}
    changes = sum(len(v) for v in textes.values() if isinstance(v, dict))
    log(_("Screens: {count} custom text(s) read from the back office", count=changes) if changes
        else _("Screens: no custom text, default texts"))
    return textes


def check(saved: dict) -> dict[str, dict[str, str]]:
    """Validates a payload from the interface (unknown screen or slot refused, length capped)."""
    if not isinstance(saved, dict):
        raise ValueError(_("an object mapping screens to their fields is expected"))
    out: dict[str, dict[str, str]] = {}
    for screen, changed in saved.items():
        if screen not in DEFAULTS:
            raise ValueError(_("unknown screen: {screen}", screen=screen))
        if not isinstance(changed, dict):
            raise ValueError(_("screen {screen}: an object is expected", screen=screen))
        for slot, text in changed.items():
            if slot not in DEFAULTS[screen]:
                raise ValueError(_("unknown field: {field}", field=f"{screen}.{slot}"))
            texte = "" if text is None else str(text).strip().replace("\n", " ")
            if len(texte) > MAX_LEN:
                raise ValueError(_("{field}: {max} characters at most",
                                   field=f"{screen}.{slot}", max=MAX_LEN))
            out.setdefault(screen, {})[slot] = texte
    return out
