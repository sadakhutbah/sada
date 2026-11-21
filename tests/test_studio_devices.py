"""Studio devices: pairing, credentials, heartbeat, manual live, exclusivity, takeover, isolation."""

from __future__ import annotations

import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import PW, client, one, rows, update

from sada.backoffice import auth, db, events_store, internal, live_input, live_watch, models, studio_devices
from sada.backoffice.api import studio as studio_api

PARIS = ZoneInfo("Europe/Paris")
HB = "/api/v1/studio/heartbeat"


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    monkeypatch.setattr(live_input, "current", lambda conn, m: {"mode": "push", "url": ""})
    studio_api.reset()


class Relay:
    """The relay as the server sees it: who publishes (None = nobody), and how many kicks it received."""

    def __init__(self):
        self.pub = None
        self.kicks = 0


@pytest.fixture
def relay(monkeypatch):
    r = Relay()
    monkeypatch.setattr(
        live_watch,
        "publisher_info",
        lambda path, timeout=1.0: (
            {"ready": True, "type": "rtmpConn", "id": "abc"} if r.pub else {"ready": False, "type": "", "id": ""}
        ),
    )

    def kick(path, timeout=2.0):
        r.kicks += 1
        r.pub = None
        return True

    monkeypatch.setattr(live_watch, "kick", kick)
    return r


# ------------------------------------------------------------------ helpers


def pair(env, name="PC mosquée", user="responsable"):
    """Full pairing: returns (device bearer, device id)."""
    anon = client(env)
    start = anon.post("/api/v1/studio/pair/start").json()
    admin = client(env, user)
    r = admin.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": name})
    assert r.status_code == 200, r.text
    poll = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]})
    assert poll.status_code == 200, poll.text
    return poll.json()["device_secret"], r.json()["device"]["id"]


def dev(env, bearer):
    return client(env, headers={"Authorization": f"Bearer {bearer}"})


def slug(env):
    return one(env["conn"], models.Mosque, id=env["mid"])["slug"]


def mosque(env):
    return one(env["conn"], models.Mosque, id=env["mid"])


def program(env, *entries):
    events_store.write(env["conn"], env["mid"], list(entries))


def oneoff(uid, minutes_from_now, title="Dars du soir", max_min=60, kind="dars"):
    when = dt.datetime.now(PARIS) + dt.timedelta(minutes=minutes_from_now)
    return {
        "uid": uid,
        "kind": kind,
        "title": title,
        "date": when.date().isoformat(),
        "time": when.strftime("%H:%M"),
        "max_min": max_min,
    }


def heartbeat(c, **body):
    return c.post(HB, json={"state": "standby", "version": "1.0.0", "camera": True, "microphone": True, **body})


def other_mosque(env):
    conn = env["conn"]
    mid2 = db.create_mosque(conn, "autre", "Autre mosquée")
    auth.create_user(conn, mid2, "admin2", PW, "admin")
    return mid2


def publish_auth(env, stream, token):
    """Status of the relay's publish-auth call for a WebRTC publisher presenting `token`."""
    body = {"action": "publish", "path": f"live/{stream}", "protocol": "webrtc", "token": token, "ip": "203.0.113.9"}
    return client(env).post("/api/v1/internal/publish-auth", json=body).status_code


def audits(env, like):
    from conftest import audit_rows

    return audit_rows(env["conn"], like=like)


# ------------------------------------------------------------------ pairing


def test_start_gives_readable_code_and_poll_is_pending(env):
    c = client(env)
    r = c.post("/api/v1/studio/pair/start")
    assert r.status_code == 200
    d = r.json()
    code = d["user_code"].replace("-", "")
    assert len(code) == 8 and set(code) <= set(studio_devices.CODE_ALPHABET) and not set(code) & set("01OIL")
    assert d["expires_in"] == 600 and len(d["device_code"]) >= 40
    p = c.post("/api/v1/studio/pair/poll", json={"device_code": d["device_code"]})
    assert p.status_code == 200 and p.json()["status"] == "pending" and p.json()["interval"] == 5


def test_pairing_flow_gives_a_credential_stored_hashed(env):
    bearer, did = pair(env)
    assert bearer.startswith(f"sdev_{did}.")
    row = one(env["conn"], models.Device, id=did)
    secret = bearer.split(".", 1)[1]
    assert secret not in str(row) and row["secret_hash"] and row["mosque_id"] == env["mid"]
    assert row["created_by"] == env["ids"]["responsable"]
    assert [a["action"] for a in audits(env, "studio.appareil.appaire")] == ["studio.appareil.appaire"]


def test_credential_can_be_fetched_again_until_the_device_speaks(env):
    anon = client(env)
    start = anon.post("/api/v1/studio/pair/start").json()
    client(env, "responsable").ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC"})
    first = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]}).json()["device_secret"]
    again = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]}).json()["device_secret"]
    assert first != again  # lost answer: a new secret replaces the unused one
    assert heartbeat(dev(env, first)).status_code == 401
    assert heartbeat(dev(env, again)).status_code == 200
    gone = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]})
    assert gone.status_code == 410  # the device has spoken: never again


def test_expired_code_is_refused(env):
    anon = client(env)
    start = anon.post("/api/v1/studio/pair/start").json()
    update(env["conn"], models.DevicePairing, {"expires_at": time.time() - 1})
    r = client(env, "responsable").ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC"})
    assert r.status_code == 410 and r.json()["code"] == "expired"
    p = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]})
    assert p.status_code == 410 and p.json()["status"] == "expired"
    assert rows(env["conn"], models.Device) == []


def test_wrong_code_is_refused_audited_and_rate_limited(env):
    admin = client(env, "responsable")
    r = admin.ecrire("/studio/devices/pair", {"user_code": "ZZZZ-ZZZZ", "name": "PC"})
    assert r.status_code == 404 and r.json()["code"] == "unknown_code"
    assert admin.ecrire("/studio/devices/pair", {"user_code": "0000", "name": "PC"}).status_code == 404
    assert len(audits(env, "studio.appairage.refuse")) == 2
    codes = [
        admin.ecrire("/studio/devices/pair", {"user_code": "ZZZZ-ZZZZ", "name": "PC"}).status_code for _ in range(12)
    ]
    assert 429 in codes  # guessing a code is throttled per account


def test_double_approval_creates_one_device(env):
    anon = client(env)
    start = anon.post("/api/v1/studio/pair/start").json()
    a, b = client(env, "responsable"), client(env, "admin")
    assert a.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC 1"}).status_code == 200
    r = b.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC 2"})
    assert r.status_code == 409 and r.json()["code"] == "already_used"
    assert len(rows(env["conn"], models.Device)) == 1


def test_pairing_needs_the_live_right_as_manager_and_csrf(env):
    start = client(env).post("/api/v1/studio/pair/start").json()
    imam = client(env, "imam")
    assert imam.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC"}).status_code == 403
    assert imam.get("/api/v1/studio/devices").status_code == 403
    resp = client(env, "responsable").post(
        "/api/v1/studio/devices/pair", json={"user_code": start["user_code"], "name": "PC"}
    )
    assert resp.status_code == 403  # no CSRF header
    assert rows(env["conn"], models.Device) == []


def test_pair_start_is_rate_limited(env):
    c = client(env)
    codes = [c.post("/api/v1/studio/pair/start").status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and 429 in codes[10:]


def test_name_is_required_and_unique_per_mosque(env):
    pair(env, "PC")
    start = client(env).post("/api/v1/studio/pair/start").json()
    admin = client(env, "responsable")
    assert admin.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": " "}).status_code == 422
    assert admin.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "pc"}).status_code == 409
    assert (
        admin.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "Autre PC"}).status_code == 200
    )


# ------------------------------------------------------------------ credentials, heartbeat, console


def test_device_credential_opens_nothing_of_the_back_office(env):
    bearer, _ = pair(env)
    d = dev(env, bearer)
    for url in ("/api/v1/users", "/api/v1/session", "/api/v1/sermons", "/api/v1/studio/devices", "/api/v1/live/regie"):
        assert d.get(url).status_code in (401, 403), url
    assert d.post("/api/v1/studio/heartbeat", json={}).status_code == 200
    assert dev(env, "sdev_1.not-the-secret-not-the-secret-xx").post(HB, json={}).status_code == 401
    assert client(env).post(HB, json={}).status_code == 401


def test_heartbeat_returns_config_schedule_and_order(env):
    program(env, oneoff("d1", 30, "Tafsir"), oneoff("d2", 90, "Fiqh"))
    bearer, _ = pair(env)
    r = heartbeat(dev(env, bearer), bitrate_kbps=0, health={"auto_login": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["command"] == "standby" and d["reason_code"] == "idle" and d["current"] is None
    assert d["next"]["title"] == "Tafsir" and d["next"]["type"] == "dars" and 1500 < d["next"]["starts_in_s"] <= 1800
    assert d["config"]["heartbeat_interval_s"] == 30 and d["config"]["start_margin_s"] == 120
    assert d["config"]["mosque"]["timezone"] == "Europe/Paris" and d["config"]["name"] == "PC mosquée"
    assert [s["title"] for s in d["slots"]] == ["Tafsir", "Fiqh"] and d["server_time"].endswith("Z")


def test_scheduled_slot_orders_live_from_the_margin(env):
    program(env, oneoff("d1", 1, "Tafsir"))  # starts within the 2-minute margin
    bearer, _ = pair(env)
    d = heartbeat(dev(env, bearer)).json()
    assert d["command"] == "live" and d["current"]["source"] == "schedule" and d["current"]["title"] == "Tafsir"
    assert d["current"]["type"] == "dars" and d["current"]["ends_at"].endswith("Z")


def test_margin_is_configurable_by_an_administrator(env):
    program(env, oneoff("d1", 10, "Tafsir"))
    bearer, _ = pair(env)
    assert heartbeat(dev(env, bearer)).json()["command"] == "standby"
    assert (
        client(env, "responsable").ecrire("/studio/settings", {"start_margin_s": 900}, methode="PUT").status_code == 403
    )
    r = client(env, "admin").ecrire("/studio/settings", {"start_margin_s": 900}, methode="PUT")
    assert r.status_code == 200 and r.json()["settings"]["start_margin_s"] == 900
    assert heartbeat(dev(env, bearer)).json()["command"] == "live"
    assert client(env, "admin").ecrire("/studio/settings", {"start_margin_s": 5}, methode="PUT").status_code == 422
    assert audits(env, "studio.reglages")


def test_console_lists_devices_with_liveness_and_rename(env):
    bearer, did = pair(env)
    heartbeat(dev(env, bearer), version="2.3.1", camera=False, bitrate_kbps=2500)
    admin = client(env, "responsable")
    d = admin.get("/api/v1/studio/devices").json()
    row = d["devices"][0]
    assert row["online"] and row["app_version"] == "2.3.1" and row["camera"] is False and row["microphone"] is True
    assert row["bitrate_kbps"] == 2500 and row["created_by_name"] and "secret_hash" not in row
    update(env["conn"], models.Device, {"last_seen_at": time.time() - 600}, id=did)
    assert admin.get("/api/v1/studio/devices").json()["devices"][0]["online"] is False
    assert admin.ecrire(f"/studio/devices/{did}", {"name": "Salle de prière"}, methode="PATCH").status_code == 200
    assert one(env["conn"], models.Device, id=did)["name"] == "Salle de prière"
    assert heartbeat(dev(env, bearer)).json()["config"]["name"] == "Salle de prière"
    assert audits(env, "studio.appareil.renomme")


def test_isolation_between_mosques(env):
    bearer, did = pair(env)
    other_mosque(env)
    intruder = client(env, "admin2")
    assert intruder.get("/api/v1/studio/devices").json()["devices"] == []
    assert intruder.ecrire(f"/studio/devices/{did}", {"name": "x"}, methode="PATCH").status_code == 404
    assert intruder.ecrire(f"/studio/devices/{did}/revoke").status_code == 404
    assert intruder.ecrire(f"/studio/devices/{did}/rotate").status_code == 404
    assert one(env["conn"], models.Device, id=did)["revoked_at"] is None
    # a code approved by the other mosque attaches the device to the other mosque
    start = client(env).post("/api/v1/studio/pair/start").json()
    assert intruder.ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC"}).status_code == 200
    poll = client(env).post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]}).json()
    assert poll["mosque"]["slug"] == "autre"
    hb2 = heartbeat(dev(env, poll["device_secret"])).json()
    assert hb2["config"]["mosque"]["slug"] == "autre" and hb2["slots"] == []
    # and a token of mosque 1 does not open mosque 2's stream
    program(env, oneoff("d1", 1))
    tok = dev(env, bearer).post("/api/v1/live/studio/token").json()["token"]
    assert publish_auth(env, slug(env), tok) == 200 and publish_auth(env, "autre", tok) == 401


def test_revocation_is_immediate_and_cuts_the_live(env, relay):
    program(env, oneoff("d1", 1))
    bearer, did = pair(env)
    d = dev(env, bearer)
    token = d.post("/api/v1/live/studio/token").json()["token"]
    assert publish_auth(env, slug(env), token) == 200  # accepted: the device now holds the stream
    relay.pub = "device"
    r = client(env, "responsable").ecrire(f"/studio/devices/{did}/revoke")
    assert r.status_code == 200 and r.json()["stream_cut"] is True and relay.kicks == 1
    refused = heartbeat(d)
    assert refused.status_code == 401 and refused.json()["code"] == "revoked"
    assert d.post("/api/v1/live/studio/token").status_code == 401
    assert publish_auth(env, slug(env), token) == 401  # the token minted before is dead too
    assert one(env["conn"], models.Device, id=did)["revoked_at"]
    assert audits(env, "studio.appareil.revoque")
    again = client(env, "responsable").ecrire(f"/studio/devices/{did}/revoke")
    assert again.status_code == 200 and relay.kicks == 1


def test_rotation_brings_a_new_credential_and_the_old_one_works_meanwhile(env):
    bearer, did = pair(env)
    d = dev(env, bearer)
    assert "credentials" not in heartbeat(d).json()
    assert client(env, "responsable").ecrire(f"/studio/devices/{did}/rotate").status_code == 200
    new = heartbeat(d).json()["credentials"]["device_secret"]
    assert new != bearer and new.startswith(f"sdev_{did}.")
    assert heartbeat(d).status_code == 200  # old one: grace period
    assert "credentials" in heartbeat(d).json()  # answer might have been lost: handed again
    newest = heartbeat(d).json()["credentials"]["device_secret"]
    assert heartbeat(dev(env, newest)).status_code == 200
    assert "credentials" not in heartbeat(dev(env, newest)).json()
    assert heartbeat(d).status_code == 401  # the old secret is gone once the new one is adopted
    assert one(env["conn"], models.Device, id=did)["rotate_requested"] == 0


# ------------------------------------------------------------------ manual live


def test_manual_start_stop_and_conflicts(env, relay):
    bearer, did = pair(env)
    d = dev(env, bearer)
    assert d.post("/api/v1/studio/live/start", json={"type": "karaoke"}).status_code == 422
    r = d.post("/api/v1/studio/live/start", json={"type": "dars", "title": "Cours improvisé"})
    assert r.status_code == 200 and r.json()["command"] == "live" and r.json()["current"]["source"] == "manual"
    hb = heartbeat(d).json()
    assert (
        hb["command"] == "live" and hb["current"] == r.json()["current"] and hb["current"]["title"] == "Cours improvisé"
    )
    assert (
        d.post("/api/v1/studio/live/start", json={"type": "dars", "title": "Cours improvisé"}).status_code == 200
    )  # retry
    assert len(rows(env["conn"], models.StudioRun, origin="manual")) == 1
    s = d.post("/api/v1/studio/live/stop")
    assert s.status_code == 200 and s.json()["stopped"] == ["manual"] and s.json()["command"] == "standby"
    assert heartbeat(d).json()["command"] == "standby"
    assert d.post("/api/v1/studio/live/stop").json()["stopped"] == []  # idempotent
    acts = [a["action"] for a in audits(env, "studio.direct.manuel.%")]
    assert acts == ["studio.direct.manuel.debut", "studio.direct.manuel.fin"]
    assert did


def test_manual_start_refused_when_a_stream_is_active(env, relay):
    bearer, _ = pair(env)
    relay.pub = "obs"
    update(env["conn"], models.Mosque, {"publish_key_hash": "x"}, id=env["mid"])
    r = dev(env, bearer).post("/api/v1/studio/live/start", json={"type": "khutba", "title": "Jumua"})
    assert r.status_code == 409 and r.json()["code"] == "live_active"
    assert "déjà en cours" not in r.json()["error"] and "already running" in r.json()["error"]
    assert "since" in r.json()  # unknown holder (OBS key): no instant to give
    assert heartbeat(dev(env, bearer)).json()["command"] == "standby"


def test_second_device_cannot_start_over_a_manual_live(env, relay):
    b1, _ = pair(env, "PC 1")
    b2, _ = pair(env, "PC 2")
    assert dev(env, b1).post("/api/v1/studio/live/start", json={"type": "dars", "title": "A"}).status_code == 200
    r = dev(env, b2).post("/api/v1/studio/live/start", json={"type": "dars", "title": "B"})
    assert r.status_code == 409 and "PC 1" in r.json()["error"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r.json()["since"])
    assert heartbeat(dev(env, b2)).json()["command"] == "standby"


def test_scheduled_slot_never_cuts_a_manual_live(env):
    bearer, _ = pair(env)
    d = dev(env, bearer)
    d.post("/api/v1/studio/live/start", json={"type": "khutba", "title": "Khutba spéciale"})
    program(env, oneoff("d1", 0, "Tafsir"))  # a slot begins while the manual live runs
    hb = heartbeat(d).json()
    assert (
        hb["command"] == "live" and hb["current"]["source"] == "manual" and hb["current"]["title"] == "Khutba spéciale"
    )


def test_manual_stop_during_a_slot_stops_it_without_restart(env):
    program(env, oneoff("d1", 0, "Tafsir"), oneoff("d2", 200, "Fiqh"))
    bearer, _ = pair(env)
    d = dev(env, bearer)
    assert heartbeat(d).json()["command"] == "live"
    s = d.post("/api/v1/studio/live/stop").json()
    assert s["stopped"] == ["slot"] and s["command"] == "standby" and s["reason_code"] == "slot_stopped"
    for _ in range(3):
        hb = heartbeat(d).json()
        assert hb["command"] == "standby" and hb["reason_code"] == "slot_stopped"
    assert hb["next"]["title"] == "Fiqh"  # the next slot is announced, not the stopped one
    assert audits(env, "studio.creneau.arret")
    # a manual live may still be started afterwards, and a manual stop then ends it
    assert d.post("/api/v1/studio/live/start", json={"type": "dars", "title": "Rappel"}).json()["command"] == "live"
    assert d.post("/api/v1/studio/live/stop").json()["stopped"] == ["manual"]
    assert heartbeat(d).json()["command"] == "standby"


def test_manual_live_ends_when_its_device_goes_silent(env):
    bearer, did = pair(env)
    dev(env, bearer).post("/api/v1/studio/live/start", json={"type": "dars", "title": "Cours"})
    update(env["conn"], models.Device, {"last_seen_at": time.time() - 3600}, id=did)
    admin = client(env, "responsable").get("/api/v1/studio/devices").json()
    assert admin["order"]["command"] == "standby"
    assert one(env["conn"], models.StudioRun, origin="manual")["ended_at"]


def test_announce_updates_title_and_type_for_the_replay(env):
    bearer, _ = pair(env)
    d = dev(env, bearer)
    assert (
        d.post("/api/v1/studio/live/announce", json={"type": "dars", "title": "x"}).status_code == 409
    )  # nothing live
    d.post("/api/v1/studio/live/start", json={"type": "dars", "title": "Provisoire"})
    r = d.post("/api/v1/studio/live/announce", json={"type": "conference", "title": "Les droits du voisin"})
    assert r.status_code == 200 and r.json()["current"]["title"] == "Les droits du voisin"
    cur = client(env, headers={"X-Internal-Token": internal.local_token()}).get("/api/v1/internal/studio-live").json()
    assert (
        cur["live"]
        and cur["type"] == "conference"
        and cur["title"] == "Les droits du voisin"
        and cur["source"] == "manual"
    )
    d.post("/api/v1/studio/live/stop")
    program(env, oneoff("d1", 0, "Tafsir"))
    d.post("/api/v1/studio/live/announce", json={"type": "khutba", "title": "Khutba du vendredi"})
    cur = client(env, headers={"X-Internal-Token": internal.local_token()}).get("/api/v1/internal/studio-live").json()
    assert cur["source"] == "schedule" and cur["title"] == "Khutba du vendredi" and cur["type"] == "khutba"
    assert audits(env, "studio.direct.annonce")


def test_only_one_device_is_ordered_live(env):
    program(env, oneoff("d1", 0, "Tafsir"))
    b1, _ = pair(env, "PC 1")
    b2, _ = pair(env, "PC 2")
    a, b = heartbeat(dev(env, b1)).json(), heartbeat(dev(env, b2)).json()
    assert (
        (a["command"], b["command"]) == ("live", "standby")
        and b["reason_code"] == "other_device"
        and "PC 1" in b["reason"]
    )


# ------------------------------------------------------------------ studio token / WHIP with a device


def test_device_token_needs_an_order_and_whip_goes_through(env, relay, monkeypatch):
    bearer, _ = pair(env)
    d = dev(env, bearer)
    r = d.post("/api/v1/live/studio/token")
    assert r.status_code == 409 and r.json()["code"] == "not_ordered"
    program(env, oneoff("d1", 0))
    ok = d.post("/api/v1/live/studio/token")
    assert ok.status_code == 200 and ok.json()["path"] == f"live/{slug(env)}" and ok.json()["expires_in"] == 120

    async def fake(method, path, token, session=None, body=b"", ctype=None):
        return httpx.Response(201, content=b"v=0 answer", headers={"Location": f"/{path}/whip/2f1c9a7e-5b1d-4c35"})

    monkeypatch.setattr(live_watch, "whip_request", fake)
    w = d.post("/api/v1/live/studio/whip", content=b"v=0\r\n", headers={"Content-Type": "application/sdp"})
    assert w.status_code == 201 and w.headers["location"].endswith("/2f1c9a7e-5b1d-4c35")
    a = audits(env, "studio.whip.debut")[0]
    assert a["user_id"] is None and "PC mosquée" in a["details"]


def test_session_path_still_works_and_is_refused_when_a_stream_is_active(env, relay):
    imam = client(env, "imam")
    assert imam.ecrire("/live/studio/token").status_code == 200
    relay.pub = "obs"
    r = imam.ecrire("/live/studio/token")
    assert r.status_code == 409 and r.json()["code"] == "live_active"


# ------------------------------------------------------------------ exclusivity and takeover


def _publish(env, **extra):
    body = {"action": "publish", "path": f"live/{slug(env)}", "ip": "203.0.113.9", **extra}
    return client(env).post("/api/v1/internal/publish-auth", json=body)


def test_second_publisher_is_refused_with_a_reason(env, relay):
    key = internal.new_publish_key(env["conn"], env["mid"])
    r = _publish(env, protocol="rtmp", password=key)
    assert r.status_code == 200  # nobody yet: accepted, and remembered
    assert one(env["conn"], models.StudioClaim, mosque_id=env["mid"])["source"] == "obs"
    relay.pub = "obs"
    bearer, _ = pair(env)
    program(env, oneoff("d1", 0))
    token = dev(env, bearer).post("/api/v1/live/studio/token")
    assert token.status_code == 409  # the device is told at the token already
    # a device token minted before the OBS stream started is still refused by the relay's auth
    relay.pub = None
    tok = dev(env, bearer).post("/api/v1/live/studio/token").json()["token"]
    relay.pub = "obs"
    r = _publish(env, protocol="webrtc", token=tok)
    assert r.status_code == 401 and "already running" in r.json()["error"] and r.json()["code"] == "live_active"
    assert r.json()["source"]["kind"] == "obs"
    r = _publish(
        env, protocol="rtmp", password=key
    )  # the OBS reconnecting is the same holder: its own stale session is replaced
    assert r.status_code == 200 and relay.kicks == 1
    assert audits(env, "studio.publication.refusee")


def test_silent_relay_api_does_not_let_a_newcomer_overwrite_the_holder(env, relay, monkeypatch):
    """The relay's API can stay mute while it waits for this very authorisation (seen on the WSL preprod)."""
    key = internal.new_publish_key(env["conn"], env["mid"])
    assert _publish(env, protocol="rtmp", password=key).status_code == 200
    relay.pub = "obs"
    bearer, _ = pair(env)
    program(env, oneoff("d1", 0))
    relay.pub = None
    tok = dev(env, bearer).post("/api/v1/live/studio/token").json()["token"]
    monkeypatch.setattr(live_watch, "publisher_info", lambda path, timeout=1.0: None)  # API unknown
    r = _publish(env, protocol="webrtc", token=tok)
    assert r.status_code == 401 and r.json()["code"] == "live_active" and r.json()["source"]["kind"] == "obs"
    assert one(env["conn"], models.StudioClaim, mosque_id=env["mid"])["source"] == "obs"  # claim untouched
    assert _publish(env, protocol="rtmp", password=key).status_code == 200  # the holder itself is not blocked


def test_wrong_key_is_still_refused_and_reading_is_untouched(env, relay):
    assert _publish(env, protocol="rtmp", password="nope").status_code == 401
    relay.pub = "obs"
    r = client(env).post(
        "/api/v1/internal/publish-auth", json={"action": "read", "path": f"live/{slug(env)}", "ip": "10.0.0.5"}
    )
    assert r.status_code == 200


def test_device_reconnecting_replaces_its_own_session(env, relay):
    program(env, oneoff("d1", 0))
    bearer, _ = pair(env)
    d = dev(env, bearer)
    t1 = d.post("/api/v1/live/studio/token").json()["token"]
    assert _publish(env, protocol="webrtc", token=t1).status_code == 200
    relay.pub = "device"
    t2 = d.post("/api/v1/live/studio/token")
    assert t2.status_code == 200  # same holder asking again
    assert _publish(env, protocol="webrtc", token=t2.json()["token"]).status_code == 200 and relay.kicks == 1


def test_takeover_is_for_an_administrator_with_confirmation_and_audit(env, relay):
    program(env, oneoff("d1", 0))
    bearer, did = pair(env)
    d = dev(env, bearer)
    t = d.post("/api/v1/live/studio/token").json()["token"]
    assert _publish(env, protocol="webrtc", token=t).status_code == 200
    relay.pub = "device"
    page = client(env, "responsable").get("/api/v1/studio/devices").json()
    assert (
        page["publisher"]["source"] == "device"
        and "PC mosquée" in page["publisher"]["text"]
        and page["can_takeover"] is False
    )
    assert client(env, "responsable").get("/api/v1/live/regie").json()["publisher"]["source"] == "device"
    assert client(env, "responsable").ecrire("/live/studio/takeover", {"confirm": True}).status_code == 403
    admin = client(env, "admin")
    assert admin.ecrire("/live/studio/takeover", {}).status_code == 422 and relay.kicks == 0  # no confirmation
    ok = admin.ecrire("/live/studio/takeover", {"confirm": True})
    assert ok.status_code == 200 and relay.kicks == 1 and ok.json()["previous"]["source"] == "device"
    rep = audits(env, "studio.reprise")
    assert len(rep) == 1 and rep[0]["user_id"] == env["ids"]["admin"]
    # the displaced device is told to stay quiet and cannot get a token meanwhile
    hb = heartbeat(d).json()
    assert hb["command"] == "standby" and hb["reason_code"] == "held"
    assert d.post("/api/v1/live/studio/token").status_code == 409
    # the administrator may now publish from the control room
    assert admin.ecrire("/live/studio/token").status_code == 200
    # nothing running: nothing to take over
    assert admin.ecrire("/live/studio/takeover", {"confirm": True}).status_code == 409


def test_takeover_never_touches_another_mosque(env, relay):
    other_mosque(env)
    relay.pub = "obs"  # a live runs, but on the relay path of the first mosque only
    seen = []
    orig = live_watch.publisher_info
    live_watch.publisher_info = lambda path, timeout=1.0: (
        seen.append(path) or (orig(path) if path.endswith(slug(env)) else {"ready": False, "type": "", "id": ""})
    )
    try:
        r = client(env, "admin2").ecrire("/live/studio/takeover", {"confirm": True})
    finally:
        live_watch.publisher_info = orig
    assert r.status_code == 409 and seen == ["live/autre"] and relay.kicks == 0


def test_mediamtx_config_refuses_a_second_publisher():
    from pathlib import Path

    import yaml

    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "deploy/helm/sada/files/mediamtx.yml").read_text())
    assert cfg["paths"]["~^live/[a-z0-9-]+$"]["overridePublisher"] is False


def test_kick_picks_the_route_of_the_publisher_kind(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        return httpx.Response(
            200,
            json={"ready": True, "source": {"type": "webRTCSession", "id": "2f1c9a7e-5b1d-4c35"}},
            request=httpx.Request("GET", url),
        )

    def fake_post(url, **kw):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(live_watch.httpx, "get", fake_get)
    monkeypatch.setattr(live_watch.httpx, "post", fake_post)
    monkeypatch.setattr(live_watch, "_api_down_at", 0.0)
    assert live_watch.kick("live/abc") is True
    assert calls == ["http://127.0.0.1:9997/v3/webrtcsessions/kick/2f1c9a7e-5b1d-4c35"]


def test_poll_names_the_public_id_and_the_secret_apart(env):
    anon = client(env)
    start = anon.post("/api/v1/studio/pair/start", json={"name": "  Salle   de prière PC "}).json()
    r = client(env, "responsable").ecrire("/studio/devices/pair", {"user_code": start["user_code"]})
    assert r.status_code == 200 and r.json()["device"]["name"] == "Salle de prière PC"  # the machine's own name
    out = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]}).json()
    did = r.json()["device"]["id"]
    assert out["device_id"] == f"sdev_{did}" and out["device_secret"].startswith(f"sdev_{did}.")
    assert out["device_secret"] != out["device_id"] and "secret" not in out["device_id"]


def test_pairing_without_any_name_is_refused(env):
    start = client(env).post("/api/v1/studio/pair/start", json={}).json()
    r = client(env, "responsable").ecrire("/studio/devices/pair", {"user_code": start["user_code"]})
    assert r.status_code == 422 and r.json()["code"] == "name_required"


def test_claimed_and_unknown_pairings_answer_410_and_404(env):
    anon = client(env)
    assert anon.post("/api/v1/studio/pair/poll", json={"device_code": "nope"}).status_code == 404
    bearer, did = pair(env)
    d = dev(env, bearer)
    assert heartbeat(d).status_code == 200
    client(env, "responsable").ecrire(f"/studio/devices/{did}/revoke")
    start = anon.post("/api/v1/studio/pair/start").json()
    client(env, "responsable").ecrire("/studio/devices/pair", {"user_code": start["user_code"], "name": "PC 2"})
    anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]})
    row = one(env["conn"], models.Device, name="PC 2")
    update(env["conn"], models.Device, {"last_seen_at": time.time()}, id=row["id"])
    gone = anon.post("/api/v1/studio/pair/poll", json={"device_code": start["device_code"]})
    assert gone.status_code == 410 and gone.json()["status"] == "claimed"


def test_unpair_by_the_device_revokes_it_and_cuts_its_live(env, relay):
    bearer, did = pair(env)
    d = dev(env, bearer)
    assert d.post("/api/v1/studio/live/start", json={"type": "dars", "title": "Cours"}).status_code == 200
    token = d.post("/api/v1/live/studio/token").json()["token"]
    assert publish_auth(env, slug(env), token) == 200
    relay.pub = "device"
    r = d.post("/api/v1/studio/unpair")
    assert r.status_code == 200 and r.json() == {"unpaired": True, "stream_cut": True}
    assert one(env["conn"], models.Device, id=did)["revoked_at"] and relay.kicks == 1
    refused = heartbeat(d)
    assert refused.status_code == 401 and refused.json()["code"] == "revoked"
    assert rows(env["conn"], models.StudioRun, origin="manual")[0]["ended_at"]
    assert audits(env, "studio.appareil.desappaire")
    assert d.post("/api/v1/studio/unpair").status_code == 401  # its credential is gone
    listed = client(env, "responsable").get("/api/v1/studio/devices").json()["devices"]
    assert all(x["device_id"].startswith("sdev_") for x in listed)
