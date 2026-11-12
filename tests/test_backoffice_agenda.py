"""Dars and conference agenda: read, create, edit and delete the JSON program."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401

from sada import events  # noqa: E402


@pytest.fixture
def prog(env):
    """Program file isolated in the test's temporary directory."""
    chemin = env["tmp"] / "programme" / "events.json"
    env["app"].state.events_file = str(chemin)
    return chemin


DARS = {"kind": "dars", "title": "Tafsir Al-Kahf", "speaker": "Imam Abdallah", "series": "Tafsir", "session": "7",
        "mode": "weekly", "weekday": "mardi", "after": "isha", "offset_min": "15", "max_min": "90", "publish": "1"}
CONF = {"kind": "conference", "title": "Les droits des voisins", "mode": "once", "date": "2099-11-21",
        "time": "18:30", "publish": "0"}


def test_liste_vide_sans_fichier(env, prog):
    r = client(env, "admin").get("/api/v1/events")
    assert r.status_code == 200
    assert r.json()["items"] == [] and "mardi" in r.json()["weekdays"]
    assert not prog.exists()


def test_creation_lue_par_le_moteur(env, prog):
    c = client(env, "admin")
    assert c.ecrire("/events", DARS).status_code == 200
    r = c.ecrire("/events", CONF)
    assert r.status_code == 200, r.text
    evs, err = events.load(prog)
    assert err is None and [e.title for e in evs] == ["Tafsir Al-Kahf", "Les droits des voisins"]
    assert evs[0].recurrence.after == "isha" and evs[0].recurrence.offset_min == 15 and evs[0].session == 7
    assert evs[1].publish is False and str(evs[1].time) == "18:30:00"
    items = c.get("/api/v1/events").json()["items"]
    assert {i["title"]: i["mode"] for i in items} == {"Tafsir Al-Kahf": "weekly", "Les droits des voisins": "once"}
    assert all(i["id"] for i in items)
    actions = [a["action"] for a in env["conn"].execute("SELECT action FROM audit").fetchall()]
    assert actions.count("programme.cree") == 2


def test_modification_et_suppression(env, prog):
    c = client(env, "admin")
    uid = c.ecrire("/events", DARS).json()["id"]
    autre = c.ecrire("/events", CONF).json()["id"]
    r = c.ecrire(f"/events/{uid}", {**DARS, "title": "Tafsir Al-Kahf (suite)", "after": "", "time": "20:00"}, "PUT")
    assert r.status_code == 200, r.text
    evs, _ = events.load(prog)
    assert evs[0].title == "Tafsir Al-Kahf (suite)" and evs[0].recurrence.time == "20:00" and evs[0].recurrence.after is None
    assert c.ecrire(f"/events/{uid}", {}, "DELETE").status_code == 200
    assert [e.uid for e in events.load(prog)[0]] == [autre]
    assert c.ecrire(f"/events/{uid}", {}, "DELETE").status_code == 404
    assert c.ecrire("/events/inconnu", DARS, "PUT").status_code == 404


@pytest.mark.parametrize("champ, valeur, mot", [
    ("title", "", "title"), ("kind", "khutba", "type"), ("weekday", "lundy", "weekday"),
    ("after", "fajr", "prayer"), ("session", "x", "session"), ("max_min", "-3", "positive"),
])
def test_refus_des_valeurs_invalides(env, prog, champ, valeur, mot):
    r = client(env, "admin").ecrire("/events", {**DARS, champ: valeur})
    assert r.status_code == 422 and mot.lower() in r.json()["message"].lower()
    assert not prog.exists()


def test_date_sans_heure_refusee(env, prog):
    r = client(env, "admin").ecrire("/events", {**CONF, "time": ""})
    assert r.status_code == 422 and "time" in r.json()["message"]


def test_droits_et_jeton(env, prog):
    imam = client(env, "imam")
    assert imam.get("/api/v1/events").status_code == 200
    assert imam.ecrire("/events", DARS).status_code == 403
    c = client(env, "responsable")
    assert c.ecrire("/events", DARS).status_code == 200
    assert c.request("POST", "/api/v1/events", json=DARS).status_code == 403      # without X-CSRF
    assert client(env).get("/api/v1/events").status_code == 401


def test_fichier_illisible_jamais_ecrase(env, prog):
    prog.parent.mkdir(parents=True)
    prog.write_text("{pas du json", encoding="utf-8")
    c = client(env, "admin")
    assert c.ecrire("/events", DARS).status_code == 422
    assert prog.read_text(encoding="utf-8") == "{pas du json"


def test_entrees_a_la_main_conservees(env, prog):
    """A hand-written event (no uid, no khutba, no unknown key) survives modifications."""
    prog.parent.mkdir(parents=True)
    prog.write_text(json.dumps({"events": [
        {"kind": "khutba", "title": "Jumua", "date": "2099-01-01", "time": "13:00", "note": "à garder"},
        {"kind": "dars", "title": "Fiqh", "recurrence": {"weekday": "jeudi", "time": "19:00"}}]}), encoding="utf-8")
    c = client(env, "admin")
    items = c.get("/api/v1/events").json()["items"]
    assert [i["title"] for i in items] == ["Fiqh"]                  # the khutba is not in the agenda
    assert c.ecrire(f"/events/{items[0]['id']}", {}, "DELETE").status_code == 200
    reste = json.loads(prog.read_text(encoding="utf-8"))["events"]
    assert len(reste) == 1 and reste[0]["note"] == "à garder"
