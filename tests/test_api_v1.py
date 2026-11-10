"""JSON API of the React front end: access, login, and response shapes."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_backoffice import PW, client  # noqa: E402,F401  (conftest provides the path)


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


def test_khutbas_cherchables_filtrees_et_paginees(env):
    """A mosque accumulates years of khutbas: search, filtering and paging are done by the server."""
    c = client(env, "admin")
    conn = env["conn"]
    base = conn.execute("SELECT mosque_id, output_dir FROM khutbas LIMIT 1").fetchone()
    for i in range(5):
        conn.execute("INSERT INTO khutbas (mosque_id, run_id, session_index, label, theme, date, output_dir, "
                     "imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                     (base["mosque_id"], f"essai{i}", i, f"Passage {i}", "la patience" if i % 2 else "la misericorde",
                      f"2024-0{i + 1}-12", base["output_dir"]))
    conn.commit()

    tout = c.get("/api/v1/sermons", params={"limit": 200}).json()
    assert tout["total"] >= 6 and tout["total"] == len(tout["items"])

    # Pagination: two disjoint pages, and the total remains that of the filter, not of the page.
    p1 = c.get("/api/v1/sermons", params={"limit": 2, "offset": 0}).json()
    p2 = c.get("/api/v1/sermons", params={"limit": 2, "offset": 2}).json()
    assert p1["total"] == p2["total"] == tout["total"] and len(p1["items"]) == len(p2["items"]) == 2
    assert {k["id"] for k in p1["items"]}.isdisjoint({k["id"] for k in p2["items"]})

    # Search: the text covers the passage, the theme and the imam — dates have their own picker.
    r = c.get("/api/v1/sermons", params={"q": "patience"}).json()
    assert r["total"] >= 2 and all("patience" in (k["theme"] or "").lower() for k in r["items"])
    assert c.get("/api/v1/sermons", params={"q": "Passage 3"}).json()["total"] == 1
    assert c.get("/api/v1/sermons", params={"q": "introuvable"}).json()["total"] == 0

    # “To validate” filter: returns only khutbas that have at least one pending clip.
    a = c.get("/api/v1/sermons", params={"pending": 1}).json()
    assert a["total"] <= tout["total"]
    assert all(int(k["pending"]) > 0 for k in a["items"])
    assert a["pending"] == a["total"]


def test_mois_de_la_liste(env):
    """The period picker is based on the months actually present, with their count."""
    c = client(env, "admin")
    tout = c.get("/api/v1/sermons", params={"limit": 200}).json()["items"]
    attendus = {}
    for k in tout:
        if k["date"]:
            attendus[k["date"][:7]] = attendus.get(k["date"][:7], 0) + 1
    r = c.get("/api/v1/sermons/mois").json()["items"]
    assert r and {m["mois"]: m["n"] for m in r} == attendus
    assert [m["mois"] for m in r] == sorted(attendus, reverse=True)   # from most recent to oldest


def test_filtre_par_mois(env):
    """Choosing a month returns only that month, and the text search combines with it."""
    c = client(env, "admin")
    mois = c.get("/api/v1/sermons/mois").json()["items"][0]["mois"]
    trouve = c.get("/api/v1/sermons", params={"mois": mois, "limit": 200}).json()
    assert trouve["total"] > 0 and all(k["date"].startswith(mois) for k in trouve["items"])
    assert c.get("/api/v1/sermons", params={"mois": "1999-01"}).json()["total"] == 0
    assert c.get("/api/v1/sermons", params={"mois": "n'importe quoi"}).json()["total"] > 0


def test_commandes_live_exposees_et_validees(env, monkeypatch):
    """The dashboard lists all commands; a value outside the list is refused before sending."""
    from minbar.backoffice import live
    envoyees = []
    live._caches.clear()                # the status cache is global: we leave nothing to the other tests
    monkeypatch.setattr(live, "_call", lambda m, meth, chemin, params=None, timeout=3.0:
                        (envoyees.append((chemin, params)) if meth == "POST" else None)
                        or {"message": "ok", "state": "KHUTBA"})
    c = client(env, "admin")
    cles = {x["key"] for x in c.get("/api/v1/dashboard").json()["live_commands"]}
    assert {"start", "pause", "resume", "auto", "stop", "extend", "skip", "cancel", "janaza", "scene"} <= cles
    h = {"X-CSRF": c.get("/api/v1/session").json()["csrf"]}
    assert c.post("/api/v1/live/extend", json={"valeur": "15"}, headers=h).status_code == 200
    assert c.post("/api/v1/live/scene", json={"valeur": "priere"}, headers=h).status_code == 200
    assert c.post("/api/v1/live/scene", json={"valeur": "inconnue"}, headers=h).status_code == 422
    assert c.post("/api/v1/live/scene", json={}, headers=h).status_code == 422
    assert c.post("/api/v1/live/extend", json={"valeur": "3"}, headers=h).status_code == 422
    assert c.post("/api/v1/live/nimporte", json={}, headers=h).status_code == 404
    assert envoyees == [("extend", {"min": "15"}), ("scene", {"name": "priere"})]
    live._caches.clear()
