"""Réglages propres à cette installation, hors base de données.

L'adresse publique d'une instance n'est pas une donnée de la mosquée : c'est la façon dont on
l'atteint depuis l'extérieur. Elle vit donc à côté du fichier du modèle, dans ``data/instance.json``,
et sert à composer les adresses que l'interface affiche (documentation, guide). Sans elle, on retombe
sur l'adresse de la requête — juste en local, faux derrière un proxy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..paths import DATA


def _file() -> Path:
    # Un chemin explicite, pour les tests et les installations qui déplacent leurs données.
    force = os.environ.get("SADA_INSTANCE_FILE")
    return Path(force) if force else DATA / "instance.json"


def lire() -> dict:
    f = _file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def public_url() -> str:
    """L'adresse saisie, sans barre finale, ou chaîne vide."""
    return str(lire().get("public_url") or "").strip().rstrip("/")


def enregistrer(public_url: str) -> str:
    valeur = (public_url or "").strip().rstrip("/")
    if valeur and not (valeur.startswith("http://") or valeur.startswith("https://")):
        raise ValueError("adresse publique attendue en http:// ou https://")
    data = lire()
    data["public_url"] = valeur
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    return valeur


def base(defaut: str) -> str:
    """L'adresse à utiliser dans les liens affichés : celle qu'on a saisie, sinon celle de la requête."""
    return public_url() or (defaut or "").rstrip("/")
