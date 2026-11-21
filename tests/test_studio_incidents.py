"""Sanity checks of the studio: incidents computed from the heartbeat, alert mails (simulated), anti-spam."""

from __future__ import annotations

import datetime as dt
import smtplib
import time
from zoneinfo import ZoneInfo

import pytest
from conftest import PW, audit_rows, client, one, rows, update

from sada import mailer
from sada.backoffice import auth, db, events_store, live_input, live_watch, models, studio_incidents
from sada.backoffice.api import studio as studio_api

HB = "/api/v1/studio/heartbeat"
PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    monkeypatch.delenv("SADA_PUBLIC_URL", raising=False)
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "instance.json"))
    monkeypatch.setattr(live_input, "current", lambda conn, m: {"mode": "push", "url": ""})
    monkeypatch.setattr(live_watch, "publisher_info", lambda path, timeout=1.0: {"ready": False, "type": "", "id": ""})
    studio_api.reset()


class Outbox(mailer.DryRunTransport):
    def to(self, address):
        return [m for m in self.sent if m.to == address]


@pytest.fixture
def box():
    return Outbox()


@pytest.fixture
def team(env):
    """Addresses: manager and administrator are recipients; the imam is not (no device right)."""
    c = env["conn"]
    for name, mail in (("responsable", "resp@mosquee.fr"), ("admin", "admin@mosquee.fr"), ("imam", "imam@mosquee.fr")):
        update(c, models.User, {"email": mail}, id=env["ids"][name])
    return env


def pair(env, name="PC"):
    anon = client(env)
    start = anon.post("/api/v1/studio/pair/start").json()
    r = client(env, "responsable").ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": name})
    bearer = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]}).json()["device_secret"]
    return client(env, headers={"Authorization": f"Bearer {bearer}"}), r.json()["device"]["id"]


def seen(env, did, t, **cols):
    update(env["conn"], models.Device, {"last_seen_at": t, **cols}, id=did)


def mosque(env, mid=None):
    return one(env["conn"], models.Mosque, id=mid or env["mid"])


def tick(env, t, box):
    return studio_incidents.tick(env["conn"], t, box)


def kinds(env, mid=None):
    return sorted(i["kind"] for i in studio_incidents.open_incidents(env["conn"], mid or env["mid"]))


NOW = time.time()


# ------------------------------------------------------------------ triggers


def test_offline_device_opens_an_incident_and_one_mail_after_the_grace(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 11 * 60)  # more than the 10 minutes of the settings
    assert tick(team, NOW, box)["opened"] == 1 and kinds(team) == ["offline"]
    assert box.sent == []  # a blink must not wake anyone: the problem has to last
    assert tick(team, NOW + 130, box)["mails"] == 2
    assert sorted(m.to for m in box.sent) == ["admin@mosquee.fr", "resp@mosquee.fr"]  # not the imam
    m = box.to("resp@mosquee.fr")[0]
    assert "PC" in m.text and "hors ligne" in m.text and "Que faire" in m.text and "Appareils" in m.text
    assert m.subject.startswith("[Sada] Mosquée Exemple : 1 problème")
    assert tick(team, NOW + 300, box)["mails"] == 0  # one mail per open problem
    acts = [a["action"] for a in audit_rows(team["conn"], like="studio.%")]
    assert "studio.incident.ouvert" in acts and "studio.mail.envoye" in acts


def test_offline_threshold_is_the_mosque_setting(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 5 * 60)
    tick(team, NOW, box)
    assert kinds(team) == []
    studio_incidents.studio_plan.save_settings(team["conn"], team["mid"], {"offline_alert_min": 3})
    tick(team, NOW, box)
    assert kinds(team) == ["offline"]


def test_missing_camera_and_microphone_and_host_checks_are_grouped_in_one_mail(team, box):
    d, did = pair(team)
    d.post(
        HB,
        json={
            "state": "standby",
            "version": "1",
            "camera": False,
            "microphone": False,
            "health": {"auto_login": False, "update_hours": True, "sleep_disabled": False},
        },
    )
    t = studio_incidents.time.time()
    tick(team, t, box)
    assert kinds(team) == ["camera", "health_auto_login", "health_sleep", "microphone"]  # `update_hours: true` is fine
    assert box.sent == []
    update(team["conn"], models.Device, {"last_seen_at": t + 125}, id=did)
    assert tick(team, t + 130, box)["mails"] == 2  # one mail per recipient, not one per problem
    text = box.to("resp@mosquee.fr")[0].text
    for needle in ("caméra", "micro", "connexion automatique", "mise en veille"):
        assert needle in text
    assert box.to("resp@mosquee.fr")[0].subject.startswith("[Sada] Mosquée Exemple : 4 problèmes")


def test_a_missing_health_field_is_not_a_problem(team, box):
    d, _ = pair(team)
    d.post(HB, json={"state": "standby", "camera": True, "microphone": True})  # old app: no health
    tick(team, time.time(), box)
    assert kinds(team) == []


def test_preflight_before_a_slot_when_the_device_is_absent(team, box):
    _, did = pair(team)
    soon = dt.datetime.now(PARIS) + dt.timedelta(minutes=20)
    events_store.write(
        team["conn"],
        team["mid"],
        [
            {
                "uid": "d",
                "kind": "dars",
                "title": "Tafsir",
                "date": soon.date().isoformat(),
                "time": soon.strftime("%H:%M"),
                "max_min": 60,
            }
        ],
    )
    t = time.time()
    seen(team, did, t - 20 * 60)
    tick(team, t, box)
    assert kinds(team) == ["offline", "preflight"]
    assert len(box.sent) == 2  # the pre-slot check is mailed at once, together with what is already open
    assert "Tafsir" in box.sent[0].text and "aucun appareil en ligne" in box.sent[0].text
    # the device comes back, complete: the preflight problem goes away
    seen(team, did, t + 5, camera_ok=1, mic_ok=1)
    tick(team, t + 10, box)
    assert kinds(team) == []


def test_preflight_also_catches_an_incomplete_device(team, box):
    d, did = pair(team)
    soon = dt.datetime.now(PARIS) + dt.timedelta(minutes=20)
    events_store.write(
        team["conn"],
        team["mid"],
        [
            {
                "uid": "d",
                "kind": "dars",
                "title": "Tafsir",
                "date": soon.date().isoformat(),
                "time": soon.strftime("%H:%M"),
            }
        ],
    )
    d.post(HB, json={"state": "standby", "camera": False, "microphone": True})
    tick(team, time.time(), box)
    assert "preflight" in kinds(team) and "camera" in kinds(team)
    assert (
        "caméra ou micro absent"
        in [
            i["detail"] for i in studio_incidents.open_incidents(team["conn"], team["mid"]) if i["kind"] == "preflight"
        ][0]
    )


def test_scheduled_live_that_does_not_start_is_an_incident(team, box, monkeypatch):
    d, _ = pair(team)
    started = dt.datetime.now(PARIS) - dt.timedelta(minutes=5)
    events_store.write(
        team["conn"],
        team["mid"],
        [
            {
                "uid": "d",
                "kind": "dars",
                "title": "Tafsir",
                "date": started.date().isoformat(),
                "time": started.strftime("%H:%M"),
                "max_min": 60,
            }
        ],
    )
    d.post(HB, json={"state": "standby", "camera": True, "microphone": True})
    tick(team, time.time(), box)
    assert kinds(team) == ["start_failed"] and len(box.to("resp@mosquee.fr")) == 1
    # the stream arrives: resolved
    monkeypatch.setattr(
        live_watch, "publisher_info", lambda path, timeout=1.0: {"ready": True, "type": "webRTCSession", "id": "x"}
    )
    d.post(HB, json={"state": "live", "camera": True, "microphone": True})
    tick(team, time.time(), box)
    assert kinds(team) == []


def test_relay_unreachable_is_not_blamed_on_the_device(team, box, monkeypatch):
    d, _ = pair(team)
    started = dt.datetime.now(PARIS) - dt.timedelta(minutes=5)
    events_store.write(
        team["conn"],
        team["mid"],
        [
            {
                "uid": "d",
                "kind": "dars",
                "title": "Tafsir",
                "date": started.date().isoformat(),
                "time": started.strftime("%H:%M"),
            }
        ],
    )
    monkeypatch.setattr(live_watch, "publisher_info", lambda path, timeout=1.0: None)  # API unknown
    d.post(HB, json={"state": "live", "camera": True, "microphone": True})
    tick(team, time.time(), box)
    assert kinds(team) == []


# ------------------------------------------------------------------ anti-spam, resolution


def test_reminders_are_limited_and_spaced(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)
    tick(team, NOW + 130, box)
    assert len(box.sent) == 2
    tick(team, NOW + 3600, box)  # too soon for a reminder
    assert len(box.sent) == 2
    tick(team, NOW + 130 + studio_incidents.REMIND_S, box)
    assert len(box.sent) == 4 and "Déjà signalés" not in box.sent[-1].text
    tick(team, NOW + 130 + 2 * studio_incidents.REMIND_S + 5, box)
    assert len(box.sent) == 6
    tick(team, NOW + 130 + 9 * studio_incidents.REMIND_S, box)
    assert len(box.sent) == 6  # reminders stop after MAX_REMINDERS


def test_a_new_problem_is_mailed_with_the_ones_already_reported(team, box):
    _, off = pair(team, "PC 1")
    d2, did2 = pair(team, "PC 2")
    seen(team, off, NOW - 3600)
    update(team["conn"], models.Device, {"last_seen_at": NOW}, id=did2)
    tick(team, NOW, box)
    tick(team, NOW + 130, box)
    assert len(box.sent) == 2 and "PC 1" in box.sent[0].text
    d2.post(HB, json={"state": "standby", "camera": False, "microphone": True})
    t = time.time()
    update(team["conn"], models.Device, {"last_seen_at": t}, id=did2)
    tick(team, t, box)
    assert len(box.sent) == 2  # the new problem is still in its grace period
    update(team["conn"], models.Device, {"last_seen_at": t + 125}, id=did2)
    tick(team, t + 130, box)
    m = box.to("resp@mosquee.fr")[-1]
    assert len(box.sent) == 4 and "PC 2 : caméra absente" in m.text and "Déjà signalés" in m.text and "PC 1" in m.text
    mails = {i["kind"]: i["mail_count"] for i in rows(team["conn"], models.StudioIncident)}
    assert mails == {"offline": 1, "camera": 1}  # the old one is not charged a reminder


def test_resolution_sends_one_resolved_mail(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)
    tick(team, NOW + 130, box)
    assert len(box.sent) == 2
    seen(team, did, NOW + 140)
    r = tick(team, NOW + 150, box)
    assert r["resolved"] == 1 and kinds(team) == []
    done = box.sent[2:]
    assert sorted(m.to for m in done) == ["admin@mosquee.fr", "resp@mosquee.fr"] and "résolu" in done[0].subject
    tick(team, NOW + 400, box)
    assert len(box.sent) == 4  # once
    acts = [a["action"] for a in audit_rows(team["conn"], like="studio.%")]
    assert "studio.incident.resolu" in acts and "studio.mail.resolu" in acts


def test_a_problem_nobody_heard_of_resolves_silently(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)  # opened, still in its grace period
    seen(team, did, NOW + 10)
    tick(team, NOW + 20, box)
    assert box.sent == [] and kinds(team) == []


def test_flapping_inside_the_grace_sends_nothing(team, box):
    d, did = pair(team)
    for i in range(4):
        d.post(HB, json={"camera": bool(i % 2), "microphone": True})
        tick(team, time.time() + i, box)
    assert box.sent == []


# ------------------------------------------------------------------ recipients, unsubscribe, isolation


def test_unsubscribe_link_and_preferences(team, box, monkeypatch):
    monkeypatch.setenv("SADA_PUBLIC_URL", "https://sada.example")
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)
    tick(team, NOW + 130, box)
    m = box.to("admin@mosquee.fr")[0]
    uid = team["ids"]["admin"]
    link = f"https://sada.example/api/v1/studio/unsubscribe?u={uid}&t={studio_incidents.unsubscribe_token(uid)}"
    assert (
        link in m.text and m.headers["List-Unsubscribe"] == f"<{link}>" and "https://sada.example/appareils" in m.text
    )
    bad = client(team).get(f"/api/v1/studio/unsubscribe?u={uid}&t=wrong")
    assert bad.status_code == 400 and one(team["conn"], models.User, id=uid)["studio_mail_optout"] == 0
    ok = client(team).get(link.removeprefix("https://sada.example"))
    assert ok.status_code == 200 and "ne recevrez plus" in ok.text
    assert one(team["conn"], models.User, id=uid)["studio_mail_optout"] == 1
    assert [a["action"] for a in audit_rows(team["conn"], like="studio.alertes.%")] == ["studio.alertes.desabonnement"]
    # the account is dropped from the next mailings; the other still receives them
    update(team["conn"], models.StudioIncident, {"mail_count": 1, "mailed_at": NOW})
    tick(team, NOW + 130 + studio_incidents.REMIND_S, box)
    assert len(box.to("admin@mosquee.fr")) == 1 and len(box.to("resp@mosquee.fr")) == 2
    # resubscribing from the console, and a bad address is refused
    admin = client(team, "admin")
    assert admin.ecrire("/studio/alerts/preferences", {"subscribed": True}, methode="PUT").status_code == 200
    assert one(team["conn"], models.User, id=uid)["studio_mail_optout"] == 0
    assert admin.ecrire("/studio/alerts/preferences", {"email": "pas une adresse"}, methode="PUT").status_code == 422
    assert admin.ecrire("/studio/alerts/preferences", {"email": "nouveau@mosquee.fr"}, methode="PUT").status_code == 200
    assert admin.get("/api/v1/studio/devices").json()["mail"] == {
        "configured": False,
        "email": "nouveau@mosquee.fr",
        "subscribed": True,
    }


def test_isolation_between_mosques(team, box):
    conn = team["conn"]
    mid2 = db.create_mosque(conn, "autre", "Autre mosquée")
    uid2 = auth.create_user(conn, mid2, "admin2", PW, "admin")
    update(conn, models.User, {"email": "admin2@autre.fr"}, id=uid2)
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)
    tick(team, NOW + 130, box)
    assert {m.to for m in box.sent} == {"resp@mosquee.fr", "admin@mosquee.fr"}  # nothing to the other mosque
    assert studio_incidents.open_incidents(conn, mid2) == []
    # the other mosque's console does not show our incident either
    page = client(team, "admin2").get("/api/v1/studio/devices").json()
    assert page["incidents"] == [] and page["devices"] == []
    # and a device of the other mosque writes to the other mosque's people only
    anon = client(team)
    start = anon.post("/api/v1/studio/pair/start").json()
    client(team, "admin2").ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC autre"})
    other = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]}).json()
    client(team, headers={"Authorization": f"Bearer {other['device_secret']}"}).post(HB, json={"camera": False})
    box.sent.clear()
    t = time.time()
    tick(team, t, box)
    update(conn, models.Device, {"last_seen_at": t + 125}, mosque_id=mid2)
    tick(team, t + 130, box)
    assert {m.to for m in box.sent} == {"admin2@autre.fr"} and "Autre mosquée" in box.sent[0].subject
    assert "PC autre" in box.sent[0].text and "resp@mosquee.fr" not in {m.to for m in box.sent}


def test_a_mosque_without_device_is_not_watched(team, box):
    assert tick(team, NOW, box) == {"mosques": 0, "opened": 0, "resolved": 0, "mails": 0}


def test_revoked_device_raises_nothing(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    client(team, "responsable").ecrire(f"/studio/devices/{did}/revoke")
    tick(team, NOW, box)
    assert kinds(team) == []


def test_incident_without_recipient_is_visible_and_not_retried(team, box):
    conn = team["conn"]
    update(conn, models.User, {"email": ""}, mosque_id=team["mid"])
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)
    tick(team, NOW + 130, box)
    assert box.sent == []
    page = client(team, "responsable").get("/api/v1/studio/devices").json()
    inc = page["incidents"][0]
    assert inc["kind"] == "offline" and inc["title"] == "L'appareil est hors ligne" and "Windows" in inc["todo"]
    assert [i["mail_count"] for i in rows(conn, models.StudioIncident)] == [1]


def test_incidents_endpoint_for_the_console(team, box):
    _, did = pair(team)
    seen(team, did, NOW - 3600)
    tick(team, NOW, box)
    r = client(team, "responsable").get("/api/v1/studio/incidents")
    assert r.status_code == 200 and r.json()[0]["kind"] == "offline"
    assert client(team, "imam").get("/api/v1/studio/incidents").status_code == 403


# ------------------------------------------------------------------ the mail sender


def test_smtp_transport_from_environment(monkeypatch):
    log = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            log.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            log.append(("starttls",))

        def login(self, user, password):
            log.append(("login", user))

        def send_message(self, msg):
            log.append(("send", msg["To"], msg["From"], msg["Subject"], msg["List-Unsubscribe"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SADA_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SADA_SMTP_USER", "bot")
    monkeypatch.setenv("SADA_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SADA_MAIL_FROM", "sada@example.org")
    assert mailer.configured() and isinstance(mailer.from_env(), mailer.SmtpTransport)
    assert mailer.send(mailer.Mail("a@b.c", "Sujet", "Corps", {"List-Unsubscribe": "<x>"})) is True
    assert log == [
        ("connect", "smtp.example", 587),
        ("starttls",),
        ("login", "bot"),
        ("send", "a@b.c", "sada@example.org", "Sujet", "<x>"),
    ]


def test_a_mail_failure_never_raises(monkeypatch):
    class Broken:
        def send(self, mail):
            raise OSError("down")

    assert mailer.send(mailer.Mail("a@b.c", "s", "t"), Broken()) is False
    monkeypatch.delenv("SADA_SMTP_HOST", raising=False)
    assert not mailer.configured() and isinstance(mailer.from_env(), mailer.DryRunTransport)


def test_failed_sending_is_retried_and_audited(team):
    class Down:
        def send(self, mail):
            raise OSError("down")

    _, did = pair(team)
    seen(team, did, NOW - 3600)
    studio_incidents.tick(team["conn"], NOW, Down())
    studio_incidents.tick(team["conn"], NOW + 130, Down())
    assert [i["mail_count"] for i in rows(team["conn"], models.StudioIncident)] == [0]  # not counted: retried
    assert "studio.mail.echec" in [a["action"] for a in audit_rows(team["conn"], like="studio.mail.%")]
