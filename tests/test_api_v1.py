"""API JSON du front React : accès, connexion, et forme des réponses."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_backoffice import PW, client  # noqa: E402,F401  (conftest assure le chemin)


def test_api_refuse_sans_session_en_json(env):
    r = client(env).get("/api/v1/session")
    assert r.status_code == 401
    assert "json" in r.headers.get("content-type", "")


def test_connexion_api_et_session(env):
    c = client(env)
    pre = c.get("/api/v1/login-challenge").json()["pre"]
    r = c.post("/api/v1/login", data={"username": "admin", "password": PW, "pre": pre})
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["user"]["role"] == "admin"
    assert "admin" in corps["user"]["permissions"]
    assert corps["mosque"]["slug"]
    assert corps["csrf"]
    assert c.get("/api/v1/session").json()["user"]["name"] == corps["user"]["name"]


def test_connexion_api_refuse_sans_jeton(env):
    c = client(env)
    r = c.post("/api/v1/login", data={"username": "admin", "password": PW})
    assert r.status_code == 403


def test_connexion_api_mauvais_mot_de_passe(env):
    c = client(env)
    pre = c.get("/api/v1/login-challenge").json()["pre"]
    r = c.post("/api/v1/login", data={"username": "admin", "password": "faux", "pre": pre})
    assert r.status_code == 401


def test_forme_des_reponses(env):
    c = client(env, "admin")
    d = c.get("/api/v1/dashboard").json()
    assert set(d["counts"]) == {"pending", "ready", "sermons"}
    assert isinstance(d["recent"], list)
    assert "ok" in d["live"]

    s = c.get("/api/v1/sermons").json()
    assert s["count"] == len(s["items"])

    cl = c.get("/api/v1/clips").json()
    assert cl["status_labels"] and "tabs" in cl

    st = c.get("/api/v1/settings").json()
    assert st["sections"][0] == "mosque"

    u = c.get("/api/v1/users").json()
    assert u["items"] and u["roles"]["admin"]

    a = c.get("/api/v1/audit").json()
    assert a["count"] == len(a["items"])


def test_un_imam_ne_voit_que_ses_khutbas(env):
    admin = client(env, "admin")
    toutes = admin.get("/api/v1/sermons").json()["count"]
    imam = client(env, "imam")
    siennes = imam.get("/api/v1/sermons").json()["count"]
    assert siennes <= toutes
    assert imam.get("/api/v1/users").status_code == 403
