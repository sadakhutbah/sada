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


def test_khutbas_cherchables_filtrees_et_paginees(env):
    """Une mosquée accumule des années de khutbas : recherche, filtre et pages sont faits par le serveur."""
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

    # Pagination : deux pages disjointes, et le total reste celui du filtre, pas celui de la page.
    p1 = c.get("/api/v1/sermons", params={"limit": 2, "offset": 0}).json()
    p2 = c.get("/api/v1/sermons", params={"limit": 2, "offset": 2}).json()
    assert p1["total"] == p2["total"] == tout["total"] and len(p1["items"]) == len(p2["items"]) == 2
    assert {k["id"] for k in p1["items"]}.isdisjoint({k["id"] for k in p2["items"]})

    # Recherche : thème, date, et rien du tout.
    r = c.get("/api/v1/sermons", params={"q": "patience"}).json()
    assert r["total"] >= 2 and all("patience" in (k["theme"] or "").lower() for k in r["items"])
    assert c.get("/api/v1/sermons", params={"q": "2024-02"}).json()["total"] == 1
    assert c.get("/api/v1/sermons", params={"q": "introuvable"}).json()["total"] == 0

    # Filtre « à valider » : ne renvoie que des khutbas qui ont au moins un clip en attente.
    a = c.get("/api/v1/sermons", params={"pending": 1}).json()
    assert a["total"] <= tout["total"]
    assert all(int(k["pending"]) > 0 for k in a["items"])
    assert a["pending"] == a["total"]


@pytest.mark.parametrize("saisie, attendu", [
    ("2026", ("date LIKE ?", "2026-%")),
    ("2026-11", ("date LIKE ?", "2026-11-%")),
    ("2026-11-06", ("date = ?", "2026-11-06")),
    ("06/11", ("date LIKE ?", "____-11-06")),
    ("6/11/2026", ("date = ?", "2026-11-06")),
    ("novembre", ("date LIKE ?", "____-11-%")),
    ("fevrier 2025", ("date LIKE ?", "2025-02-%")),
    ("6 novembre 2026", ("date = ?", "2026-11-06")),
    ("nov 2026", ("date LIKE ?", "2026-11-%")),
    ("Novembre 2026", ("date LIKE ?", "2026-11-%")),
    ("patience", None),          # du texte, pas une date
    ("11", None),                # un nombre seul n'est pas une date
    ("", None),
])
def test_motif_date(saisie, attendu):
    """La recherche d'une date doit accepter ce qu'un humain tape, pas le format de la base."""
    from minbar.backoffice.api_v1 import motif_date
    assert motif_date(saisie) == attendu


def test_recherche_par_date_en_toutes_lettres(env):
    """« novembre 2026 » ou « 06/11 » doivent trouver les khutbas du jour, comme « 2026-11-06 »."""
    c = client(env, "admin")
    jour = c.get("/api/v1/sermons", params={"limit": 200}).json()["items"][0]["date"]
    assert jour, "le jeu d'essai doit dater ses khutbas"
    annee, mois, jour_ = jour.split("-")
    for saisie in (jour, f"{jour_}/{mois}/{annee}", f"{int(jour_)}/{int(mois)}", f"{annee}-{mois}",
                   annee, f"{mois}/{annee}"):
        trouve = c.get("/api/v1/sermons", params={"q": saisie, "limit": 200}).json()
        assert jour in {k["date"] for k in trouve["items"]}, saisie
    assert c.get("/api/v1/sermons", params={"q": "novembre 1999"}).json()["total"] == 0
