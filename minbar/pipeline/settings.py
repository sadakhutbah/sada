"""Réglages du traitement d'après-direct, partagés par le back-office et le pipeline.

Le back-office écrit ce fichier (Réglages → Traitement) ; le pipeline le lit à son démarrage,
comme il lit déjà `data/llm.json` pour le modèle. Un réglage donné en ligne de commande ou par
l'environnement reste prioritaire : c'est la règle unique de `minbar.config.pick`.
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
    return Path(os.environ.get("MINBAR_PIPELINE_CONFIG") or paths.DATA / "pipeline.json")


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


def clip_count(cli: int | None = None) -> int:
    """Extraits par khutba : option, puis MINBAR_CLIPS, puis le fichier, puis 3."""
    raw = pick(cli, os.environ.get("MINBAR_CLIPS"), config().get("clips"), DEFAULT_CLIPS)
    try:
        return borne(raw)
    except (TypeError, ValueError):   # fichier abîmé : le pipeline garde son défaut
        return DEFAULT_CLIPS


def save_config(clips) -> int:
    """Écrit le réglage et rend la valeur retenue. Les autres clés du fichier sont conservées."""
    n = borne(clips)
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {**config(), "clips": n}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return n
