"""Le point d'entrée ASGI doit rester utilisable par la commande officielle `fastapi run`."""
from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path


def test_entree_declaree_dans_pyproject():
    racine = Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((racine / "pyproject.toml").read_text())
    entree = cfg["tool"]["fastapi"]["entrypoint"]
    module, _, attribut = entree.partition(":")
    assert module == "minbar.backoffice.asgi"
    assert attribut == "app"


def test_asgi_construit_l_application(tmp_path, monkeypatch):
    monkeypatch.setenv("MINBAR_DB", str(tmp_path / "asgi.db"))
    monkeypatch.setenv("MINBAR_IMPORT_EVERY", "0")
    sys.modules.pop("minbar.backoffice.asgi", None)
    mod = importlib.import_module("minbar.backoffice.asgi")
    assert mod.app.title.startswith("Minbar Live")
    assert mod.app.state.secure is False
    assert (tmp_path / "asgi.db").exists()          # la base est créée au démarrage
