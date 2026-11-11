"""Réglages du traitement d'après-direct, partagés par le back-office et le pipeline.

Le back-office écrit ce fichier (Réglages → Traitement) ; le pipeline le lit à son démarrage,
comme il lit déjà `data/llm.json` pour le modèle. Un réglage donné en ligne de commande ou par
l'environnement reste prioritaire : c'est la règle unique de `sada.config.pick`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import paths
from ..config import pick

DEFAULT_CLIPS = 3                 # extraits par khutba quand rien n'est réglé
MIN_CLIPS = 1
MAX_CLIPS = 10


def config_path() -> Path:
    return Path(os.environ.get("SADA_PIPELINE_CONFIG") or paths.DATA / "pipeline.json")


def config() -> dict:
    """Réglages enregistrés depuis le back-office. Fichier absent ou illisible : {}."""
    try:
        d = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def borne(valeur) -> int:
    """Nombre d'extraits ramené entre MIN_CLIPS et MAX_CLIPS ; texte non numérique refusé."""
    return max(MIN_CLIPS, min(MAX_CLIPS, int(valeur)))


def clips_enabled() -> bool:
    """Interrupteur enregistré : la production d'extraits, active par défaut."""
    return bool(config().get("clips_enabled", True))


def _flag(raw, default: bool = True) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "oui")


def arabic_retry_enabled(cli: bool | None = None) -> bool:
    """Rattrapage des passages arabes mal transcrits, actif par défaut.

    Priorité : option de commande, puis SADA_ARABIC_RETRY, puis le fichier de réglages
    (`arabic_retry`), puis True.
    """
    raw = pick(cli, os.environ.get("SADA_ARABIC_RETRY"))
    if raw is not None:
        return _flag(raw)
    return _flag(config().get("arabic_retry"), True)


def clip_count(cli: int | None = None) -> int:
    """Extraits par khutba, 0 signifiant « aucun ».

    Priorité : option de commande, puis SADA_CLIPS (0 désactive), puis le fichier (interrupteur
    puis nombre), puis DEFAULT_CLIPS. Un fichier abîmé laisse le défaut.
    """
    raw = pick(cli, os.environ.get("SADA_CLIPS"))
    if raw is not None:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_CLIPS
        return 0 if n <= 0 else borne(n)
    if not clips_enabled():
        return 0
    try:
        return borne(config().get("clips", DEFAULT_CLIPS))
    except (TypeError, ValueError):
        return DEFAULT_CLIPS


def save_config(clips, enabled=True) -> dict:
    """Écrit les réglages et rend ce qui a été retenu. Les autres clés du fichier sont conservées.

    Le nombre est gardé même quand on désactive : réactiver retrouve le compte précédent.
    """
    n = borne(clips)
    garde = bool(enabled)
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {**config(), "clips": n, "clips_enabled": garde}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return {"clips": n, "enabled": garde}
