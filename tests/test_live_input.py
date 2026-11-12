"""Video input of the live: the two modes, validation, the interface endpoint, the internal one, the engine."""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401  (conftest provides the path)

CAM = "rtsp://192.168.1.20:554/stream1"


# --- stored choices ---------------------------------------------------------------------------

def test_camera_est_le_mode_par_defaut(env):
    from sada.backoffice import live_input as li
    d = client(env, "admin").get("/api/v1/settings").json()["input"]
    assert d["mode"] == "camera" and d["configured"] is False
    assert d["url"].startswith("rtsp://")
    assert li.effective(env["conn"], env["mid"]) == ""       # nothing chosen yet: local setting wins


def test_mode_poussee_fabrique_l_adresse_de_l_obs(env):
    from sada.backoffice import live_input as li
    c = client(env, "admin")
    r = c.ecrire("/settings/input", {"mode": "push"}, methode="PUT")
    assert r.status_code == 200, r.text
    d = r.json()["input"]
    assert d["mode"] == "push"
    from sada.backoffice import db
    mosque = db.one(env["conn"], "SELECT * FROM mosques WHERE id = ?", env["mid"])
    assert d["push_rtmp"].startswith("rtmp://") and d["push_rtmp"].endswith(":1935/live/" + li.slug(mosque))
    # what the encoder reads is the relay's RTSP copy, never the RTMP address
    assert d["url"].startswith("rtsp://127.0.0.1:8554/live/")
    assert li.effective(env["conn"], env["mid"]) == d["url"]


def test_poussee_ne_demande_pas_d_adresse(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/input", {"mode": "push", "url": "n'importe quoi"}, methode="PUT")
    assert r.status_code == 200
    assert r.json()["input"]["url"].startswith("rtsp://127.0.0.1:8554/")   # the typed value is ignored


def test_adresse_de_camera_refusee(env):
    c = client(env, "admin")
    for url, mot in (("https://exemple.fr/cam", "rtsp"), ("rtsp://", "server"), ("rtsp://a b/cam", "space")):
        r = c.ecrire("/settings/input", {"mode": "camera", "url": url}, methode="PUT")
        assert r.status_code == 422 and mot in r.json()["message"], (url, r.text)
    r = c.ecrire("/settings/input", {"mode": "autre"}, methode="PUT")
    assert r.status_code == 422 and "camera" in r.json()["message"]


def test_camera_enregistree_puis_relue(env):
    from sada.backoffice import live_input as li
    c = client(env, "admin")
    c.ecrire("/settings/input", {"mode": "camera", "url": CAM}, methode="PUT")
    assert c.get("/api/v1/settings").json()["input"]["url"] == CAM
    assert li.effective(env["conn"], env["mid"]) == CAM


def test_reserve_a_l_admin(env):
    for role in ("imam", "responsable"):
        assert client(env, role).ecrire("/settings/input", {"mode": "push"}, methode="PUT").status_code == 403


# --- machine to machine -----------------------------------------------------------------------

def test_endpoint_interne_livre_l_entree(env):
    from sada.backoffice import internal
    c = client(env, "admin")
    c.ecrire("/settings/input", {"mode": "camera", "url": CAM}, methode="PUT")
    assert c.get("/api/v1/internal/live-input").status_code == 403
    bad = c.get("/api/v1/internal/live-input", headers={internal.HEADER: "faux"})
    assert bad.status_code == 403
    d = c.get("/api/v1/internal/live-input", headers={internal.HEADER: internal.token()}).json()
    assert d["url"] == CAM and d["mosque_id"] == env["mid"]


def test_endpoint_interne_vide_si_rien_n_est_choisi(env):
    from sada.backoffice import internal
    d = client(env, "admin").get("/api/v1/internal/live-input", headers={internal.HEADER: internal.token()}).json()
    assert d["url"] == ""


# --- engine side ------------------------------------------------------------------------------

def test_orchestrateur_lit_l_entree_du_back_office():
    from sada import source
    seen = {}

    def fetch(url, token):
        seen["url"], seen["token"] = url, token
        return {"url": CAM}

    assert source.from_backoffice("http://127.0.0.1:8090", 8088, fetch=fetch) == CAM
    assert "/api/v1/internal/live-input" in seen["url"] and "port=8088" in seen["url"]
    assert seen["token"]


def test_entree_absente_ou_panne_garde_la_configuration_locale():
    from sada import source
    assert source.from_backoffice("http://127.0.0.1:8090", 0, fetch=lambda u, t: {"url": " "}) == ""
    assert source.from_backoffice("", 0) == ""

    def boom(url, token):
        raise OSError("connexion refusée")

    said = []
    assert source.from_backoffice("http://127.0.0.1:8090", 0, fetch=boom, log=said.append) == ""
    assert said and "connexion refusée" in said[0]


def test_option_de_transport_seulement_pour_rtsp():
    from sada import source
    assert source.input_options("rtmp://127.0.0.1:1935/live/obs") == []
    assert source.input_options("rtsp://127.0.0.1:8554/live/obs") == ["-rtsp_transport", "tcp"]


def test_encodeur_accepte_une_entree_rtmp():
    from sada.config import Config
    from sada.encoder import Encoder
    cfg = Config.load(None)
    cfg.camera_url = "rtmp://127.0.0.1:1935/live/obs"
    cmd = Encoder(cfg, Path("/tmp")).ffmpeg_cmd("/tmp/x.mkv", None)
    assert "-rtsp_transport" not in cmd
    assert "rtmp://127.0.0.1:1935/live/obs" in cmd


# --- reachability test ------------------------------------------------------------------------

def test_sonde_flux_injoignable_est_expliquee():
    from sada.backoffice import live_input as li
    ok, detail = li.probe("rtsp://127.0.0.1:1/rien", timeout=2)
    assert ok is False and detail


def test_sonde_refuse_de_verifier_une_poussee():
    from sada.backoffice import live_input as li
    ok, detail = li.probe("rtmp://127.0.0.1:1935/live/obs")
    assert ok is False and "OBS" in detail


def test_la_mise_en_route_n_est_proposee_qu_une_fois(env):
    """First login only: once completed or skipped, everything stays in Settings."""
    from sada.backoffice import internal
    c = client(env, "admin")
    assert c.get("/api/v1/session").json()["mosque"]["needs_onboarding"] is True
    r = c.ecrire("/settings/onboarding", {}, methode="PUT")
    assert r.status_code == 200 and r.json()["needs_onboarding"] is False
    assert c.get("/api/v1/session").json()["mosque"]["needs_onboarding"] is False
    assert c.ecrire("/settings/onboarding", {"done": "0"}, methode="PUT").json()["needs_onboarding"] is True
    assert client(env, "responsable").ecrire("/settings/onboarding", {}, methode="PUT").status_code == 403
    assert internal.token()          # le jeton interne n'a pas bougé


def test_bouton_tester(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/input/test", {"mode": "camera", "url": "rtsp://127.0.0.1:1/rien"}, methode="POST")
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["detail"]
    r = c.ecrire("/settings/input/test", {"mode": "camera", "url": "https://x.fr"}, methode="POST")
    assert r.status_code == 422
