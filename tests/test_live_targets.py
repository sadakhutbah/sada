"""Live destinations set from the back office: masking, validation, internal endpoint, engine side."""

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401  (conftest provides the path)

FB = "rtmps://live-api-s.facebook.com:443/rtmp/CLE-FB"
YT = "rtmps://a.rtmp.youtube.com/live2/CLE-YT"


# --- masking and validation -------------------------------------------------------------------

def test_masque_la_cle_et_garde_le_serveur():
    from sada.backoffice import destinations as d
    assert d.mask(FB) == "rtmps://live-api-s.facebook.com:443/rtmp/" + "••••••"
    assert d.mask(YT) == "rtmps://a.rtmp.youtube.com/live2/" + "••••••"
    assert d.mask("") == "" and d.mask("pas-une-adresse") == "pas-une-adresse"


def test_adresses_refusees():
    from sada.backoffice import destinations as d
    assert d.check("")[1]                       # empty
    assert "rtmp" in d.check("https://exemple.fr/live")[1]
    assert d.check(FB) == (FB, "")
    assert "server" in d.check("rtmps:///rtmp/cle")[1]
    assert "key" in d.check("rtmps://a.rtmp.youtube.com/")[1]      # nothing after the host


# --- screen side ------------------------------------------------------------------------------

def test_destination_enregistree_puis_affichee_masquee(env):
    from sada.backoffice import db
    c = client(env, "admin")
    r = c.ecrire("/settings/live", {"targets": [{"label": "Facebook", "url": FB, "enabled": True},
                                                {"label": "YouTube", "url": YT, "enabled": False}]}, methode="PUT")
    assert r.status_code == 200, r.text
    rows = r.json()["targets"]
    assert [x["label"] for x in rows] == ["Facebook", "YouTube"]
    assert rows[0]["enabled"] and not rows[1]["enabled"]
    assert "CLE-FB" not in r.text and "CLE-YT" not in r.text
    assert rows[0]["url"].endswith(db.MASK)
    # the key is stored in the database, never in the answer
    assert db.one(env["conn"], "SELECT url FROM live_targets WHERE label = 'Facebook'")["url"] == FB
    # the screen reads them back through /settings
    d = c.get("/api/v1/settings").json()
    assert [x["label"] for x in d["targets"]] == ["Facebook", "YouTube"]


def test_masque_renvoye_conserve_la_cle(env):
    from sada.backoffice import db
    c = client(env, "admin")
    c.ecrire("/settings/live", {"targets": [{"label": "Facebook", "url": FB, "enabled": True}]}, methode="PUT")
    row = c.get("/api/v1/settings").json()["targets"][0]
    # the screen sends the masked value back, with a new name: the key must survive
    r = c.ecrire("/settings/live", {"targets": [{**row, "label": "Page de la mosquée"}]}, methode="PUT")
    assert r.status_code == 200, r.text
    m = db.one(env["conn"], "SELECT label, url FROM live_targets WHERE id = ?", row["id"])
    assert m["label"] == "Page de la mosquée" and m["url"] == FB


def test_nouvelle_cle_est_acceptee(env):
    c = client(env, "admin")
    c.ecrire("/settings/live", {"targets": [{"label": "Facebook", "url": FB, "enabled": True}]}, methode="PUT")
    row = c.get("/api/v1/settings").json()["targets"][0]
    again = FB.replace("CLE-FB", "CLE-NEUVE")
    c.ecrire("/settings/live", {"targets": [{**row, "url": again}]}, methode="PUT")
    from sada.backoffice import db
    assert db.one(env["conn"], "SELECT url FROM live_targets WHERE id = ?", row["id"])["url"] == again


def test_refus_expliques(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/live", {"targets": [{"label": "x", "url": "https://exemple.fr/live"}]}, methode="PUT")
    assert r.status_code == 422 and "rtmp" in r.json()["message"]
    r = c.ecrire("/settings/live", {"targets": [{"url": FB}, {"url": FB}]}, methode="PUT")
    assert r.status_code == 422 and "already" in r.json()["message"]
    r = c.ecrire("/settings/live", {"targets": [{"url": FB}] * 7}, methode="PUT")
    assert r.status_code == 422 and "6" in r.json()["message"]
    # nothing was written by a refused save
    assert c.get("/api/v1/settings").json()["targets"] == []


def test_retirer_une_destination(env):
    c = client(env, "admin")
    c.ecrire("/settings/live", {"targets": [{"label": "A", "url": FB}, {"label": "B", "url": YT}]}, methode="PUT")
    rows = c.get("/api/v1/settings").json()["targets"]
    r = c.ecrire("/settings/live", {"targets": [rows[0]]}, methode="PUT")
    assert [x["label"] for x in r.json()["targets"]] == ["A"]


def test_reserve_a_l_admin(env):
    for role in ("imam", "responsable"):
        r = client(env, role).ecrire("/settings/live", {"targets": []}, methode="PUT")
        assert r.status_code == 403


# --- machine to machine -----------------------------------------------------------------------

def test_endpoint_interne_livre_les_cles_actives(env):
    from sada.backoffice import internal
    c = client(env, "admin")
    c.ecrire("/settings/live", {"targets": [{"label": "A", "url": FB, "enabled": True},
                                            {"label": "B", "url": YT, "enabled": False}]}, methode="PUT")
    anon = c.get("/api/v1/internal/live-targets")
    assert anon.status_code == 403
    bad = c.get("/api/v1/internal/live-targets", headers={internal.HEADER: "faux"})
    assert bad.status_code == 403
    d = c.get("/api/v1/internal/live-targets", headers={internal.HEADER: internal.token()}).json()
    assert d["urls"] == [FB]                    # enabled only, key in clear, order kept
    assert d["mosque_id"] == env["mid"]


# --- engine side ------------------------------------------------------------------------------

def test_orchestrateur_lit_les_destinations_du_back_office():
    from sada import destinations as d
    seen = {}

    def fetch(url, token):
        seen["url"] = url, token
        return {"urls": [FB, " ", YT]}

    got = d.from_backoffice("http://127.0.0.1:8090", 8088, fetch=fetch)
    assert got == [FB, YT]
    assert "/api/v1/internal/live-targets" in seen["url"][0] and "port=8088" in seen["url"][0]
    assert seen["url"][1]


def test_aucune_destination_ou_panne_garde_la_configuration_locale():
    from sada import destinations as d
    assert d.from_backoffice("http://127.0.0.1:8090", 0, fetch=lambda u, t: {"urls": []}) == []
    assert d.from_backoffice("", 0) == []

    def boom(url, token):
        raise OSError("connexion refusée")

    said = []
    assert d.from_backoffice("http://127.0.0.1:8090", 0, fetch=boom, log=said.append) == []
    assert said and "connexion refusée" in said[0]
