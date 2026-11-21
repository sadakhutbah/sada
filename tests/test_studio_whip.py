"""WHIP signalling proxied through the back office (token endpoint, offer, ICE, close) with a simulated relay."""

import json

import httpx
import pytest
from conftest import client, one

from sada.backoffice import live_input, live_watch, models, studio_token

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


def _ask(c, slug, **q):
    body = {"action": "publish", "path": f"live/{slug}", "protocol": "webrtc", "ip": "203.0.113.9", **q}
    return c.post("/api/v1/internal/publish-auth", json=body).status_code


class Seen(list):
    def __init__(self):
        super().__init__()
        self.state = {"status": 201}


@pytest.fixture
def relay(monkeypatch):
    """Fake relay: records WHIP calls (method, path, auth, content type, body); its own check of the
    bearer is the real publish-auth logic, so a refused token shows up as the relay's 401."""
    seen = Seen()
    state = seen.state

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.port,
                request.url.path,
                request.headers.get("authorization"),
                request.headers.get("content-type"),
                request.content,
            )
        )
        if request.method == "POST":
            return httpx.Response(
                state["status"], content=b"v=0 answer", headers={"Location": f"{request.url.path}/{SESSION}"}
            )
        return httpx.Response(204 if request.method == "PATCH" else 200)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr(live_watch.httpx, "AsyncClient", Fake)
    return seen


# --- token endpoint ----------------------------------------------------------------------------


def test_token_endpoint_needs_session_csrf_and_live_right(env, relay):
    assert client(env).post("/api/v1/live/studio/token").status_code in (401, 403)
    imam = client(env, "imam")
    assert imam.post("/api/v1/live/studio/token").status_code == 403  # no CSRF
    admin = client(env, "admin")
    uid = env["ids"]["imam"]
    assert admin.ecrire(f"/users/{uid}", {"permissions": json.dumps(["validate"])}, methode="PATCH").status_code == 200
    assert client(env, "imam").ecrire("/live/studio/token").status_code == 403


def test_token_endpoint_issues_bound_token(env, relay):
    mosque = one(env["conn"], models.Mosque, id=env["mid"])
    r = client(env, "imam").ecrire("/live/studio/token")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["path"] == f"live/{mosque['slug']}" and d["expires_in"] == 120
    claims = studio_token.verify(d["token"], mosque["slug"])
    assert claims["u"] == env["ids"]["imam"] and claims["m"] == mosque["id"]
    assert _ask(client(env), mosque["slug"], token=d["token"]) == 200
    log = one(env["conn"], models.Audit, action="studio.jeton")
    assert log["user_id"] == env["ids"]["imam"] and log["mosque_id"] == mosque["id"]


# --- WHIP proxy --------------------------------------------------------------------------------


def _offer(c, body=SDP):
    return c.post(
        "/api/v1/live/studio/whip", content=body, headers={"Content-Type": "application/sdp", "X-CSRF": c.csrf or ""}
    )


def test_whip_requires_session_csrf_and_right(env, relay):
    assert _offer(client(env)).status_code in (401, 403)
    imam = client(env, "imam")
    r = imam.post("/api/v1/live/studio/whip", content=SDP, headers={"Content-Type": "application/sdp"})
    assert r.status_code == 403
    assert relay == []


def test_whip_offer_relayed_with_token_of_own_mosque(env, relay):
    mosque = one(env["conn"], models.Mosque, id=env["mid"])
    imam = client(env, "imam")
    r = _offer(imam)
    assert r.status_code == 201, r.text
    assert r.content == b"v=0 answer"
    assert r.headers["location"] == f"/api/v1/live/studio/whip/{SESSION}"
    method, port, path, auth, ctype, body = relay[0]
    assert (method, port, path) == ("POST", live_watch.RELAY_WEBRTC_PORT, f"/live/{mosque['slug']}/whip")
    assert ctype == "application/sdp" and body == SDP
    claims = studio_token.verify(auth.removeprefix("Bearer "), mosque["slug"])
    assert claims["u"] == env["ids"]["imam"] and claims["m"] == mosque["id"]
    log = one(env["conn"], models.Audit, action="studio.whip.debut")
    assert log["user_id"] == env["ids"]["imam"] and json.loads(log["details"])["session"] == SESSION


def test_whip_ice_and_close(env, relay):
    mosque = one(env["conn"], models.Mosque, id=env["mid"])
    imam = client(env, "imam")
    h = {"X-CSRF": imam.csrf}
    frag = b"a=candidate:1 1 UDP 1 192.0.2.1 5000 typ host\r\n"
    r = imam.patch(f"/api/v1/live/studio/whip/{SESSION}", content=frag, headers={**h, "Content-Type": ICE})
    assert r.status_code == 204
    assert relay[-1][:3] == ("PATCH", live_watch.RELAY_WEBRTC_PORT, f"/live/{mosque['slug']}/whip/{SESSION}")
    assert relay[-1][4] == ICE and relay[-1][5] == frag
    assert studio_token.verify(relay[-1][3].removeprefix("Bearer "), mosque["slug"])
    bad = imam.patch(f"/api/v1/live/studio/whip/{SESSION}", content=frag, headers={**h, "Content-Type": "text/plain"})
    assert bad.status_code == 415
    assert imam.delete(f"/api/v1/live/studio/whip/{SESSION}", headers=h).status_code == 200
    assert relay[-1][:3] == ("DELETE", live_watch.RELAY_WEBRTC_PORT, f"/live/{mosque['slug']}/whip/{SESSION}")
    assert one(env["conn"], models.Audit, action="studio.whip.fin")
    assert imam.delete("/api/v1/live/studio/whip/..%2Fx", headers=h).status_code in (404, 405)
    assert imam.delete("/api/v1/live/studio/whip/x", headers=h).status_code == 404  # not a session id


def test_whip_relay_errors(env, relay):
    imam = client(env, "imam")
    assert _offer(imam, b"").status_code == 400
    assert _offer(imam, b"x" * (64 * 1024 + 1)).status_code == 400
    relay.state["status"] = 401
    assert _offer(imam).status_code == 502
    relay.state["status"] = 400
    assert _offer(imam).status_code == 409


def test_whip_unreachable_relay(env, monkeypatch):
    def boom(*a, **kw):
        raise live_watch.RelayUnavailable("down")

    monkeypatch.setattr(live_watch, "_send", boom)
    assert _offer(client(env, "imam")).status_code == 503


def test_whip_refused_when_input_is_not_push(env, relay, monkeypatch):
    monkeypatch.setattr(live_input, "current", lambda conn, m: {"mode": "camera", "url": "rtsp://x/y"})
    imam = client(env, "imam")
    assert _offer(imam).status_code == 409
    assert imam.ecrire("/live/studio/token").status_code == 409
    assert relay == []
