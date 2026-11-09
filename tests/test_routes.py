"""Adresses anglaises et permanence des anciennes adresses françaises."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401  (conftest assure le chemin)

PAGES = ["/", "/sermons", "/clips", "/publications", "/audit-log", "/account",
         "/api/folders", "/admin/users", "/admin/mosque", "/health"]


def test_les_pages_repondent_en_anglais(env):
    c = client(env, "admin")
    for url in PAGES:
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)


def test_la_page_de_connexion_est_en_anglais(env):
    r = client(env).get("/login")
    assert r.status_code == 200
    assert 'action="/login"' in r.text


def test_les_onglets_des_reglages_sont_en_anglais(env):
    c = client(env, "admin")
    for sec in ("mosque", "logo", "live", "folders", "model", "connectors", "access"):
        assert c.get(f"/admin/mosque?section={sec}").status_code == 200, sec


def test_aucune_adresse_francaise_dans_les_pages(env):
    """Les gabarits ne doivent plus pointer vers les anciennes adresses."""
    c = client(env, "admin")
    vieux = ("/connexion", "/deconnexion", "/khutbas", "/mosquee", "/modele",
             "/connecteurs", "/utilisateurs", "/dossiers", "/sante", "/journal", "/compte")
    for url in PAGES + ["/admin/mosque?section=folders", "/sermons"]:
        page = c.get(url).text
        for fragment in vieux:
            assert fragment not in page, (url, fragment)


def test_les_anciennes_adresses_redirigent(env):
    c = client(env, "admin")
    cas = {"/journal": "/audit-log", "/sante": "/health", "/compte": "/account",
           "/api/dossiers": "/api/folders", "/admin/utilisateurs": "/admin/users",
           "/admin/mosquee?section=model": "/admin/mosque?section=model"}
    for vieux, neuf in cas.items():
        r = c.get(vieux, follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308), (vieux, r.status_code)
        assert r.headers["location"] == neuf, (vieux, r.headers.get("location"))


def test_anciennes_adresses_a_parametre(env):
    c = client(env, "admin")
    kid = env["kid"]
    cid = env["conn"].execute("SELECT id FROM clips ORDER BY id LIMIT 1").fetchone()["id"]
    assert c.get(f"/khutbas/{kid}", follow_redirects=False).headers["location"] == f"/sermons/{kid}"
    assert c.post(f"/clips/{cid}/publier", follow_redirects=False).headers["location"] == f"/clips/{cid}/publish"
    assert c.post(f"/admin/connecteurs/local", follow_redirects=False).headers["location"] == "/admin/connectors/local"
