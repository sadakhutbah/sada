"""Helpers and fixtures shared by the back-office tests (defined in test_backoffice)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backoffice import client, csrf, env, login, make_run  # noqa: E402,F401


@pytest.fixture(autouse=True)
def transcription_locale(monkeypatch, tmp_path):
    """Les tests tournent sur le moteur local, quel que soit le réglage de la machine.

    Sans cela, un poste configuré pour la transcription distante (`SADA_TRANSCRIPTION_MODE`, ou un
    `data/transcription.json` de production) enverrait les fichiers de test à un vrai fournisseur.
    Les tests du distant posent eux-mêmes leurs variables.
    """
    for name in [k for k in os.environ if k.startswith("SADA_TRANSCRIPTION_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "transcription-absent.json"))
