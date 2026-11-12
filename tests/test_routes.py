"""Single interface: the React front end at the root, and old addresses kept working."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401  (conftest provides the path)


def test_le_front_est_servi_a_la_racine(env):
    c = client(env)
    r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="root"' in r.text and "Sadā" in r.text
    assert "/src/main.tsx" not in r.text  # production build, not the dev server
    asset = re.search(r'src="(/assets/index-[^"]+\.js)"', r.text).group(1)
    assert c.get(asset).status_code == 200


# What a browser sends when it opens an address by hand: this is what distinguishes
# a navigation from an API call (the server only returns HTML for the former).
NAVIGATION = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def test_les_adresses_du_front_retombent_sur_l_index(env):
    """The front-end router handles its own addresses: the server no longer renders any page."""
    c = client(env)
    for url in ("/khutbas", "/clips", "/settings", "/khutbas/12", "/clips/8"):
        r = c.get(url, headers=NAVIGATION)
        assert r.status_code == 200 and 'id="root"' in r.text, url


def test_une_adresse_api_inconnue_repond_du_json_pas_du_html(env):
    """The fallback to the index only concerns navigation: an API error remains a JSON error."""
    c = client(env)
    r = c.get("/api/v1/inconnue")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_plus_aucune_page_html_rendue_par_le_serveur(env):
    """/login and /logout are no longer pages: login goes through the API, the screen through the front end."""
    c = client(env)
    page = c.get("/login", headers=NAVIGATION).text
    assert 'action="/login"' not in page and 'name="csrf"' not in page
    assert 'id="root"' in page


def test_les_anciennes_adresses_redirigent(env):
    c = client(env, "admin")
    cas = {
        "/journal": "/audit",
        "/sante": "/health",
        "/api/dossiers": "/api/v1/folders",
        "/admin/utilisateurs": "/users",
        "/admin/mosquee": "/settings",
        "/sermons": "/khutbas",
    }
    for vieux, neuf in cas.items():
        r = c.get(vieux, follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308), (vieux, r.status_code)
        assert r.headers["location"] == neuf, (vieux, r.headers.get("location"))


def test_anciennes_adresses_a_parametre(env):
    c = client(env, "admin")
    kid = env["kid"]
    cid = env["conn"].execute("SELECT id FROM clips ORDER BY id LIMIT 1").fetchone()["id"]
    assert c.get(f"/sermons/{kid}", follow_redirects=False).headers["location"] == f"/khutbas/{kid}"
    assert c.post(f"/clips/{cid}/publier", follow_redirects=False).headers["location"] == f"/api/v1/clips/{cid}/publish"
    assert c.post("/admin/connecteurs/local", follow_redirects=False).headers["location"] == "/settings"


def test_les_anciennes_adresses_francaises_ne_sont_que_des_redirections(env):
    c = client(env)
    for url in ("/connexion", "/deconnexion", "/compte", "/publication"):
        assert c.get(url, follow_redirects=False).status_code == 308, url
