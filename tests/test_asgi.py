"""The ASGI entry point must remain usable by the official `fastapi run` command."""
from __future__ import annotations

import importlib
import sys


def test_asgi_construit_l_application(tmp_path, monkeypatch):
    monkeypatch.setenv("MINBAR_DB", str(tmp_path / "asgi.db"))
    monkeypatch.setenv("MINBAR_IMPORT_EVERY", "0")
    sys.modules.pop("minbar.backoffice.asgi", None)
    mod = importlib.import_module("minbar.backoffice.asgi")
    assert mod.app.title.startswith("Minbar Live")
    assert mod.app.state.secure is False
    assert (tmp_path / "asgi.db").exists()          # the database is created at startup
