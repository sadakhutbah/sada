"""Hosted mode: the settings the platform keeps for itself.

The bucket, the folders of the machine, the public address and the orchestrator are the same for
every mosque of the hosting: a mosque administrator neither sees them in the screen nor reaches
them through the API. The server is the only judge of that, the screen just applies its answers.
"""

from __future__ import annotations

import pytest
from test_backoffice import PW, client
from test_storage_upload import BON

from sada.backoffice import auth, db, internal, storage


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_MODE", "hosted")
    # The platform settings live beside the code: never the instance file of the machine running the tests.
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "instance.json"))


@pytest.fixture
def chef(env):
    """A mosque administrator who is not the platform operator (`admin` is)."""
    uid = auth.create_user(env["conn"], env["mid"], "chef", PW, "admin", "Chef de la mosquée")
    return uid


def connected(env, username):
    """Signs in, then picks the mosque: hosted mode never opens one by itself."""
    c = client(env, username)
    assert c.ecrire("/mosques/select", {"mosque_id": env["mid"]}).status_code == 200
    return c


def test_l_admin_d_une_mosquee_ne_voit_ni_le_compartiment_ni_les_dossiers(env, hosted, chef):
    d = connected(env, "chef").get("/api/v1/settings").json()
    assert d["sections"] == ["mosque", "logo", "screens", "live", "processing"]
    assert "storage" not in d
    assert d["locked_fields"] == ["public_url", "orchestrator_url", "orchestrator_token"]


def test_l_operateur_de_la_plateforme_voit_tout(env, hosted):
    d = connected(env, "admin").get("/api/v1/settings").json()
    assert {"replays", "folders", "model", "transcription"} <= set(d["sections"])
    assert d["locked_fields"] == [] and "storage" in d


def test_les_champs_de_la_plateforme_ne_sont_pas_meme_livres(env, hosted, chef):
    """Not shown, not sent back: the payload carries an empty value, never the real setting."""
    d = connected(env, "chef").get("/api/v1/settings").json()
    assert d["mosque"]["orchestrator_url"] == "" and d["mosque"]["orchestrator_token"] == ""
    assert d["guide"]["public_url"] == ""


def test_le_serveur_refuse_les_reglages_de_la_plateforme(env, hosted, chef):
    c = connected(env, "chef")
    for chemin, corps, methode in (
        ("/settings/storage", BON, "PUT"),
        ("/settings/instance", {"public_url": "https://pris.example"}, "PUT"),
        ("/settings/mosque", {"orchestrator_url": "http://127.0.0.1:9999"}, "PATCH"),
        ("/settings/mosque", {"output_dir": "/tmp/ailleurs"}, "PATCH"),
    ):
        r = c.ecrire(chemin, corps, methode=methode)
        assert r.status_code == 403, (chemin, r.status_code)
    assert c.get("/api/v1/folders").status_code == 403


def test_l_admin_d_une_mosquee_regle_toujours_le_reste(env, hosted, chef):
    """The rest of the mosque form still saves: the lock is about four settings, not the screen."""
    c = connected(env, "chef")
    r = c.ecrire("/settings/mosque", {"name": "Mosquée Renommée", "timezone": "Europe/Paris"}, "PATCH")
    assert r.status_code == 200
    assert c.get("/api/v1/settings").json()["mosque"]["name"] == "Mosquée Renommée"


def test_un_seul_compartiment_et_un_prefixe_par_mosquee(env, hosted):
    """One bucket for the whole platform; each mosque writes under `<prefix><slug>/`."""
    c = connected(env, "admin")
    assert c.ecrire("/settings/storage", BON, methode="PUT").status_code == 200
    vue = c.get("/api/v1/settings").json()["storage"]
    assert vue["bucket"] == "sada-rediffusions" and vue["secret_key"] == db.MASK
    # The engine gets the platform's bucket and its own folder, keys in clear for boto3 only.
    d = c.get("/api/v1/internal/storage", headers={internal.HEADER: internal.token()}).json()
    assert d["target"]["bucket"] == "sada-rediffusions"
    assert d["target"]["prefix"] == "rediffusions/ma-mosquee/"
    assert d["target"]["secret_key"] == "secret-tres-secret"
    # A second mosque shares the bucket and never the folder.
    other = db.create_mosque(env["conn"], "autre-mosquee", "Autre Mosquée")
    cible = storage.effective(env["conn"], other) or {}
    assert cible["prefix"] == "rediffusions/autre-mosquee/" and cible["bucket"] == "sada-rediffusions"


def test_sans_compartiment_de_plateforme_les_rediffusions_restent_locales(env, hosted):
    """Nothing configured: no target, the engine keeps the recordings on the machine."""
    assert storage.effective(env["conn"], env["mid"]) is None
