"""Studio publish token: signed (user + mosque, short-lived), accepted by publish-auth for WebRTC only."""

import json

import pytest
from conftest import client, rows

from sada.backoffice import db, internal, live_input, models, studio_token

SDP = b"v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\n"
ICE = "application/trickle-ice-sdpfrag"
SESSION = "2f1c9a7e-5b1d-4c35"


@pytest.fixture(autouse=True)
def _secret(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    monkeypatch.delenv(studio_token.SECRET_ENV, raising=False)
    monkeypatch.delenv(studio_token.TTL_ENV, raising=False)
    # the hosted platform offers push only; the default test install would read a camera
    monkeypatch.setattr(live_input, "current", lambda conn, m: {"mode": "push", "url": ""})


@pytest.fixture
def second(env):
    db.create_mosque(env["conn"], "other", "Other mosque")


def _mosques(env):
    ms = rows(env["conn"], models.Mosque, order_by=models.mosques.c.id)
    return ms[0], ms[1]


def _ask(c, slug, **q):
    body = {"action": "publish", "path": f"live/{slug}", "protocol": "webrtc", "ip": "203.0.113.9", **q}
    return c.post("/api/v1/internal/publish-auth", json=body).status_code


# --- token -------------------------------------------------------------------------------------


def test_token_roundtrip_and_tampering():
    mosque = {"id": 7, "slug": "mosquee-a"}
    tok, life = studio_token.issue(3, mosque, now=1000)
    assert life == 120
    assert studio_token.verify(tok, "mosquee-a", now=1050) == {"u": 3, "m": 7, "s": "mosquee-a", "exp": 1120}
    assert studio_token.verify(tok, "mosquee-b", now=1050) is None  # other stream
    assert studio_token.verify(tok, "mosquee-a", now=1121) is None  # expired
    prefix, body, sig = tok.split(".")
    forged = studio_token._b64(json.dumps({"u": 3, "m": 8, "s": "mosquee-b", "exp": 9**9}).encode())
    assert studio_token.verify(f"{prefix}.{forged}.{sig}", "mosquee-b") is None  # body changed
    assert studio_token.verify(f"{prefix}.{body}.{sig[:-2]}xx", "mosquee-a", now=1050) is None
    for junk in ("", None, "a.b", "a.b.c.d", "sdst1..", "sdst1.e30.AAAA"):
        assert studio_token.verify(junk, "mosquee-a") is None


def test_token_depends_on_secret(monkeypatch):
    tok, _ = studio_token.issue(1, {"id": 1, "slug": "m"})
    monkeypatch.setenv(studio_token.SECRET_ENV, "another-secret")
    assert studio_token.verify(tok, "m") is None
    tok2, _ = studio_token.issue(1, {"id": 1, "slug": "m"})
    assert studio_token.verify(tok2, "m")


def test_ttl_is_clamped(monkeypatch):
    monkeypatch.setenv(studio_token.TTL_ENV, "100000")
    assert studio_token.ttl() == 600
    monkeypatch.setenv(studio_token.TTL_ENV, "1")
    assert studio_token.ttl() == 30
    monkeypatch.setenv(studio_token.TTL_ENV, "abc")
    assert studio_token.ttl() == studio_token.DEFAULT_TTL


# --- publish-auth ------------------------------------------------------------------------------


def test_publish_auth_accepts_token_for_its_mosque_only(env, second):
    a, b = _mosques(env)
    c = client(env)
    uid = env["ids"]["imam"]
    tok, _ = studio_token.issue(uid, a)
    assert _ask(c, a["slug"], token=tok) == 200
    assert _ask(c, b["slug"], token=tok) == 401  # another mosque
    assert _ask(c, a["slug"], token=tok, protocol="rtmp") == 401  # WebRTC only
    assert _ask(c, a["slug"], token=tok, protocol="srt") == 401
    assert _ask(c, a["slug"], token=tok + "x") == 401  # forged
    assert _ask(c, a["slug"], token="") == 401
    expired, _ = studio_token.issue(uid, a, now=1)
    assert _ask(c, a["slug"], token=expired) == 401
    ghost, _ = studio_token.issue(999999, a)
    assert _ask(c, a["slug"], token=ghost) == 401  # account gone


def test_token_of_other_mosque_cannot_publish_here(env, second):
    a, b = _mosques(env)
    c = client(env)
    tok_b, _ = studio_token.issue(env["ids"]["imam"], b)
    assert _ask(c, b["slug"], token=tok_b) == 200
    assert _ask(c, a["slug"], token=tok_b) == 401
    assert internal.studio_publish_allowed(env["conn"], a["slug"], tok_b) is False


def test_permanent_key_still_works(env, second):
    a, b = _mosques(env)
    c = client(env)
    key = internal.new_publish_key(env["conn"], a["id"])
    for proto in ("rtmp", "srt", "webrtc"):
        assert _ask(c, a["slug"], password=key, protocol=proto) == 200
    assert _ask(c, b["slug"], password=key) == 401
    assert _ask(c, a["slug"], password="nope") == 401
    assert _ask(c, a["slug"]) == 401
