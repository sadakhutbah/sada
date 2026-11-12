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
    assert set(d["counts"]) == {"pending", "ready", "sermons", "dars"}
    assert isinstance(d["recent"], list)
    assert "ok" in d["live"]

    s = c.get("/api/v1/sermons").json()
    assert s["count"] == len(s["items"])

    cl = c.get("/api/v1/clips").json()
    assert cl["tabs"] == ["a_valider", "valide", "publie", "rejete"] and "status_labels" not in cl

    st = c.get("/api/v1/settings").json()
    assert st["sections"][0] == "mosque"

    u = c.get("/api/v1/users").json()
    assert u["items"] and u["roles"]["admin"]

    a = c.get("/api/v1/audit").json()
    assert a["count"] == len(a["items"])


def test_les_clips_arrivent_par_pages(env):
    """Three hundred clips must not be rendered at once: the page asks for twenty, then follows."""
    conn, kid, mid = env["conn"], env["kid"], env["mid"]
    for i in range(3, 28):
        conn.execute(
            """INSERT INTO clips (mosque_id, khutba_id, idx, kind, why, start, end, file_path,
                        status, version, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                kid,
                i,
                "verset",
                "test",
                i * 10.0,
                i * 10.0 + 40,
                f"/tmp/c{i}.mp4",
                "a_valider" if i % 2 else "valide",
                1,
                "2026-11-10T10:00:00",
                "2026-11-10T10:00:00",
            ),
        )
    conn.commit()
    c = client(env, "admin")

    page = c.get("/api/v1/clips?limit=10").json()
    assert len(page["items"]) == 10 and page["offset"] == 0
    assert page["total"] == 27 and page["count"] == 10
    assert sum(page["counts"].values()) == 27

    suite = c.get("/api/v1/clips?limit=10&offset=20").json()
    assert len(suite["items"]) == 7 and suite["total"] == 27
    assert {x["id"] for x in page["items"]}.isdisjoint({x["id"] for x in suite["items"]})

    filtre = c.get("/api/v1/clips?status=a_valider&limit=10&offset=0").json()
    assert filtre["total"] == filtre["counts"]["a_valider"] == 15  # 13 ajoutés + 2 du jeu de départ
    assert all(x["status"] == "a_valider" for x in filtre["items"])

    assert c.get("/api/v1/clips?limit=0").status_code == 422


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
        conn.execute(
            "INSERT INTO khutbas (mosque_id, run_id, session_index, label, theme, date, output_dir, "
            "imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                base["mosque_id"],
                f"essai{i}",
                i,
                f"Passage {i}",
                "la patience" if i % 2 else "la misericorde",
                f"2024-0{i + 1}-12",
                base["output_dir"],
            ),
        )
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
    assert [m["mois"] for m in r] == sorted(attendus, reverse=True)  # from most recent to oldest


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
    from sada.backoffice import live

    envoyees = []
    live._caches.clear()  # the status cache is global: we leave nothing to the other tests
    monkeypatch.setattr(
        live,
        "_call",
        lambda m, meth, chemin, params=None, timeout=3.0: (
            (envoyees.append((chemin, params)) if meth == "POST" else None) or {"message": "ok", "state": "KHUTBA"}
        ),
    )
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


def test_le_tableau_de_bord_n_envoie_aucun_texte_d_interface(env):
    """Contract: command keys and choice values only, plus a danger flag. Labels are the front's job."""
    from sada.state import EXTEND_MINUTES, PICKABLE_SCENES

    c = client(env, "admin")
    d = c.get("/api/v1/dashboard").json()
    assert "state_label" not in d["live"] and "sessions" not in d["live"]
    cmds = d["live_commands"]
    assert [x["key"] for x in cmds] == [
        "start",
        "pause",
        "resume",
        "auto",
        "extend",
        "skip",
        "scene",
        "janaza",
        "stop",
        "cancel",
    ]
    for x in cmds:
        assert set(x) == {"key", "destructive", "choices"}  # no label, style or confirm sentence
        assert all(isinstance(v, str) for v in x["choices"])
    assert {x["key"] for x in cmds if x["destructive"]} == {"stop", "cancel"}
    assert next(x for x in cmds if x["key"] == "extend")["choices"] == list(EXTEND_MINUTES)
    assert next(x for x in cmds if x["key"] == "scene")["choices"] == list(PICKABLE_SCENES)


def test_le_journal_n_envoie_que_les_cles_d_action(env):
    c = client(env, "admin")
    d = c.get("/api/v1/audit").json()
    assert "labels" not in d
    assert "connexion" in d["actions"] and all(isinstance(a, str) for a in d["actions"])
