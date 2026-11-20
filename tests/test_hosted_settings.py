"""Hosted mode: the platform's settings belong to the operator, outside the mosques.

The bucket, the folders of the machine, the public address, the model and the transcription are the
platform's. A mosque administrator neither sees them in its own screen nor reaches them through the
API: `/api/v1/platform/...` answers 403, and the mosque's form refuses those fields. The server is
the only judge; the screens just apply its answers.
"""

from __future__ import annotations

import pytest
from conftest import one
from test_backoffice import PW, client
from test_storage_upload import BON

from sada.backoffice import auth, db, internal, storage
from sada.backoffice.models import Mosque


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


def test_le_reglage_d_une_mosquee_est_le_meme_pour_tous(env, hosted, chef):
    """One list of tabs, no field locked by role: the mosque's screen is the mosque's."""
    chef_view = connected(env, "chef").get("/api/v1/settings").json()
    operateur_view = connected(env, "admin").get("/api/v1/settings").json()
    assert chef_view["sections"] == operateur_view["sections"]
    assert chef_view["sections"] == ["mosque", "prayer", "logo", "screens", "live", "processing", "tarawih"]
    assert "locked_fields" not in chef_view and "locked_fields" not in operateur_view
    assert not {"storage", "model", "transcription", "folders", "public_url"} & set(chef_view)
    assert set(chef_view["mosque"]) == set(operateur_view["mosque"])
    assert not {"orchestrator_url", "orchestrator_token", "output_dir"} & set(chef_view["mosque"])


def test_l_operateur_ouvre_les_reglages_de_la_plateforme_sans_mosquee(env, hosted):
    """No mosque in the session: the platform screen is the installation's, not a mosque's."""
    c = client(env, "admin")
    d = c.get("/api/v1/platform/settings").json()
    assert d["hosted"] is True
    assert d["sections"] == ["replays", "folders", "public_url", "model", "transcription"]
    # No mosque open, so no orchestrator tab here: the picker carries it, per mosque.
    assert d["mosque"] is None and d["public_url"] == ""
    assert {"storage", "folders", "model", "transcription"} <= set(d)


def test_l_admin_d_une_mosquee_est_refuse_par_la_plateforme(env, hosted, chef):
    c = connected(env, "chef")
    for chemin, corps, methode in (
        ("/platform/storage", BON, "PUT"),
        ("/platform/public-url", {"public_url": "https://pris.example"}, "PUT"),
        ("/platform/model", {"url": "http://127.0.0.1:1/v1"}, "PUT"),
        ("/platform/transcription", {"mode": "local"}, "PUT"),
        ("/platform/folders", {"output": "/tmp/ailleurs"}, "PUT"),
        (f"/platform/mosques/{env['mid']}/orchestrator", {"url": "", "token": ""}, "PUT"),
    ):
        r = c.ecrire(chemin, corps, methode=methode)
        assert r.status_code == 403, (chemin, r.status_code)
    for chemin in ("/api/v1/platform/settings", "/api/v1/platform/folders", "/api/v1/platform/mosques"):
        assert c.get(chemin).status_code == 403, chemin


def test_le_reglage_de_la_mosquee_refuse_les_champs_de_la_plateforme(env, hosted, chef):
    """The mosque form does not know those fields: refused, for the operator too, never dropped."""
    for utilisateur in ("chef", "admin"):
        c = connected(env, utilisateur)
        for champ, valeur in (("orchestrator_url", "http://127.0.0.1:9999"), ("output_dir", "/tmp/ailleurs")):
            r = c.ecrire("/settings/mosque", {champ: valeur}, "PATCH")
            assert r.status_code == 422, (utilisateur, champ)
            assert "platform" in r.json()["message"], (utilisateur, champ)


def test_l_admin_d_une_mosquee_regle_toujours_ses_propres_reglages(env, hosted, chef):
    """The mosque's form still saves: what moved is the installation's screen, not this one."""
    c = connected(env, "chef")
    r = c.ecrire("/settings/mosque", {"name": "Mosquée Renommée", "timezone": "Europe/Paris"}, "PATCH")
    assert r.status_code == 200
    assert c.get("/api/v1/settings").json()["mosque"]["name"] == "Mosquée Renommée"


def test_l_operateur_regle_l_orchestrateur_d_une_mosquee_depuis_la_liste(env, hosted):
    """The picker's panel: address and token of one mosque, never through the mosque's settings."""
    c = client(env, "admin")
    liste = c.get("/api/v1/platform/mosques").json()["mosques"]
    assert [m["slug"] for m in liste] == ["ma-mosquee"]
    r = c.ecrire(
        f"/platform/mosques/{env['mid']}/orchestrator",
        {"url": "http://127.0.0.1:8089", "token": "secret-agent"},
        methode="PUT",
    )
    assert r.status_code == 200, r.text
    m = liste[0]
    assert m["orchestrator_token"] == "" and m["has_token"] is False
    relu = c.get("/api/v1/platform/mosques").json()["mosques"][0]
    assert relu["orchestrator_url"] == "http://127.0.0.1:8089" and relu["has_token"] is True
    assert relu["orchestrator_token"] == db.MASK
    assert one(env["conn"], Mosque, id=env["mid"])["orchestrator_token"] == "secret-agent"
    # an address that is not http(s) is refused
    assert (
        c.ecrire(f"/platform/mosques/{env['mid']}/orchestrator", {"url": "pas-une-adresse"}, "PUT").status_code == 422
    )


def test_un_seul_compartiment_et_un_prefixe_par_mosquee(env, hosted):
    """One bucket for the whole platform; each mosque writes under `<prefix><slug>/`."""
    c = connected(env, "admin")
    assert c.ecrire("/platform/storage", BON, methode="PUT").status_code == 200
    vue = c.get("/api/v1/platform/settings").json()["storage"]
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


def test_les_dossiers_de_la_plateforme_valent_pour_chaque_mosquee(env, hosted):
    """Hosted: the roots are the machine's; a mosque writes under its slug, like its bucket prefix."""
    c = client(env, "admin")
    racine = env["tmp"] / "plateforme"
    r = c.ecrire("/platform/folders", {"output": str(racine), "publish": str(racine / "pub")}, "PUT")
    assert r.status_code == 200, r.text
    m = one(env["conn"], Mosque, id=env["mid"])
    assert db.mosque_dirs(m)["output"] == racine / "ma-mosquee"
    db.create_mosque(env["conn"], "autre-mosquee", "Autre Mosquée")
    other = one(env["conn"], Mosque, slug="autre-mosquee")
    assert db.mosque_dirs(other)["output"] == racine / "autre-mosquee"
    # nothing was written into the mosque: the roots stay the platform's
    assert other["output_dir"] == ""


def test_sans_compartiment_de_plateforme_les_rediffusions_restent_locales(env, hosted):
    """Nothing configured: no target, the engine keeps the recordings on the machine."""
    assert storage.effective(env["conn"], env["mid"]) is None
