"""Tarawih settings: audio by default, video refused without confirmation, nothing outside the period."""

import pytest

pytest.importorskip("fastapi")

from conftest import client  # noqa: E402

from sada.backoffice import models, orm, tarawih  # noqa: E402

FORM = {"mode": "audio", "start": "2027-02-08", "end": "2027-03-09", "offset_min": "20", "max_min": "90", "imam": ""}


def test_valeurs_par_defaut_en_son_seul(env):
    d = client(env, "admin").get("/api/v1/settings").json()["tarawih"]
    assert d["mode"] == "audio" and d["start"] == "" and d["offset_min"] == 20 and d["max_min"] == 90
    assert tarawih.event(env["conn"], env["mid"]) is None  # no period: nothing planned


def test_video_refusee_sans_confirmation(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/tarawih", {**FORM, "mode": "video"}, methode="PUT")
    assert r.status_code == 422
    assert tarawih.event(env["conn"], env["mid"]) is None
    r = c.ecrire("/settings/tarawih", {**FORM, "mode": "video", "confirm_video": "1"}, methode="PUT")
    assert r.status_code == 200, r.text
    assert r.json()["tarawih"]["video_confirmed_at"]
    assert tarawih.event(env["conn"], env["mid"])["mode"] == "video"
    log = orm.first(env["conn"], models.Audit, action="tarawih.configure")
    assert log and '"video"' in (log.get("details") or "")


def test_reglage_audio_donne_une_nuit_par_soir_dans_la_periode(env):
    c = client(env, "admin")
    assert c.ecrire("/settings/tarawih", {**FORM, "imam": "Imam Exemple"}, methode="PUT").status_code == 200
    raw = tarawih.event(env["conn"], env["mid"])
    assert raw["mode"] == "audio" and raw["speaker"] == "Imam Exemple"
    assert raw["recurrence"] == {"after": "isha", "offset_min": 20}
    assert raw["period"] == {"from": "2027-02-08", "until": "2027-03-09"}


@pytest.mark.parametrize(
    "patch",
    [
        {"end": "2027-02-01"},
        {"end": "2027-05-01"},
        {"end": ""},
        {"offset_min": "0"},
        {"max_min": "999"},
        {"mode": "always"},
    ],
)
def test_reglages_invalides_refuses(env, patch):
    r = client(env, "admin").ecrire("/settings/tarawih", {**FORM, **patch}, methode="PUT")
    assert r.status_code == 422


def test_mode_off_ne_planifie_rien(env):
    c = client(env, "admin")
    assert c.ecrire("/settings/tarawih", {**FORM, "mode": "off"}, methode="PUT").status_code == 200
    assert tarawih.event(env["conn"], env["mid"]) is None


def test_video_sans_confirmation_en_base_reste_en_son_seul(env):
    orm.upsert(
        env["conn"],
        models.Tarawih,
        ("mosque_id",),
        mosque_id=env["mid"],
        mode="video",
        start_date="2027-02-08",
        end_date="2027-03-09",
    )
    assert tarawih.event(env["conn"], env["mid"])["mode"] == "audio"
