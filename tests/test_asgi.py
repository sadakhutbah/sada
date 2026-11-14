"""The ASGI entry point must remain usable by the official `fastapi run` command."""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.mark.skipif(bool(__import__("os").environ.get("SADA_DATABASE_URL")), reason="SQLite file format")
def test_asgi_construit_l_application(tmp_path, monkeypatch):
    monkeypatch.setenv("SADA_DB", str(tmp_path / "asgi.db"))
    monkeypatch.setenv("SADA_IMPORT_EVERY", "0")
    sys.modules.pop("sada.backoffice.asgi", None)
    mod = importlib.import_module("sada.backoffice.asgi")
    assert mod.app.title.startswith("Sadā")
    assert mod.app.state.secure is False
    assert (tmp_path / "asgi.db").exists()  # the database is created at startup
