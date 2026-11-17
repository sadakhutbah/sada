"""Control room: the monitors are proxied to the relay behind the session and the `live` right,
for the session's mosque only; the relay's API answers the back office only."""

import json

import httpx
import pytest
from conftest import PW, client, one

from sada.backoffice import auth, db, live, live_watch, models

SDP_OFFER = b"v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\n"


@pytest.fixture
def relay(monkeypatch, tmp_path):
    """Fake relay: records the requests the back office forwards, answers like mediamtx."""
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    monkeypatch.setattr(live_watch, "_api_down_at", 0.0)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.port, request.url.path, dict(request.url.params)))
        path = request.url.path
        if request.url.port == live_watch.RELAY_WEBRTC_PORT and request.method == "POST":
            return httpx.Response(201, content=b"v=0 answer", headers={"Location": f"{path}/2f1c9a7e-5b1d-4c35"})
        if request.url.port == live_watch.RELAY_WEBRTC_PORT and request.method == "DELETE":
            return httpx.Response(200)
        if request.url.port == live_watch.RELAY_HLS_PORT:
            if path.endswith("index.m3u8"):
                return httpx.Response(200, content=b"#EXTM3U\nmain_stream.m3u8?session=5c9798b1-bac6\n")
            return httpx.Response(200, content=b"#EXTM3U\nseg0.mp4?session=5c9798b1-bac6\n")
        if request.url.port == live_watch.RELAY_API_PORT:
            body = {"ready": True, "readers": [{}, {}], "inboundBytes": 1000}
            return httpx.Response(200, content=json.dumps(body).encode())
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_async = httpx.AsyncClient

    class FakeAsync(real_async):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    def fake_get(url, **kw):
        with httpx.Client(transport=transport) as c:
            return c.get(url, **kw)

    monkeypatch.setattr(live_watch.httpx, "AsyncClient", FakeAsync)
    monkeypatch.setattr(live_watch.httpx, "get", fake_get)
    monkeypatch.setattr(live, "status_cached", lambda mosque, ttl=0, forcer=False: {"ok": True, "state": "IDLE"})
    return seen


def _slug(env):
    return one(env["conn"], models.Mosque, id=env["mid"])["slug"]


def _offer(c, which="program"):
    return c.post(
        f"/api/v1/live/watch/{which}/whep",
        content=SDP_OFFER,
        headers={"Content-Type": "application/sdp", "X-CSRF": getattr(c, "csrf", "") or ""},
    )


def test_refused_without_session(env, relay):
    c = client(env)
    assert c.get("/api/v1/live/regie").status_code == 401
    assert _offer(c).status_code in (401, 403)
    assert c.get("/api/v1/live/watch/program/hls/index.m3u8").status_code == 401
    assert relay == []


def test_refused_without_live_right(env, relay):
    admin = client(env, "admin")
    uid = env["ids"]["imam"]
    assert admin.ecrire(f"/users/{uid}", {"permissions": json.dumps(["validate"])}, methode="PATCH").status_code == 200
    imam = client(env, "imam")
    assert imam.get("/api/v1/live/regie").status_code == 403
    assert _offer(imam).status_code == 403
    assert imam.get("/api/v1/live/watch/source/hls/index.m3u8").status_code == 403
    assert relay == []


def test_whep_needs_csrf(env, relay):
    c = client(env, "imam")
    r = c.post("/api/v1/live/watch/program/whep", content=SDP_OFFER, headers={"Content-Type": "application/sdp"})
    assert r.status_code == 403
    assert relay == []


def test_whep_proxied_to_own_mosque(env, relay):
    c = client(env, "imam")
    r = _offer(c)
    assert r.status_code == 201, r.text
    assert r.text == "v=0 answer"
    slug = _slug(env)
    # the relay path comes from the session's mosque; the Location is rewritten to the proxy
    assert relay[0][:3] == ("POST", live_watch.RELAY_WEBRTC_PORT, f"/live/{slug}-prog/whep")
    assert r.headers["location"] == "/api/v1/live/watch/program/whep/2f1c9a7e-5b1d-4c35"
    d = c.delete(r.headers["location"], headers={"X-CSRF": c.csrf})
    assert d.status_code == 200
    assert relay[-1][:3] == ("DELETE", live_watch.RELAY_WEBRTC_PORT, f"/live/{slug}-prog/whep/2f1c9a7e-5b1d-4c35")


def test_source_follows_push_input(env, relay, monkeypatch):
    monkeypatch.setattr(live_watch.live_input, "current", lambda conn, m: {"mode": "push", "url": ""})
    c = client(env, "admin")
    assert _offer(c, "source").status_code == 201
    assert relay[0][2] == f"/live/{_slug(env)}/whep"


def test_unknown_monitor_and_bad_names(env, relay):
    c = client(env, "admin")
    assert _offer(c, "raw").status_code == 404
    for name in ("..%2F..%2Fother%2Findex.m3u8", "index.txt", "a b.ts"):
        assert c.get(f"/api/v1/live/watch/program/hls/{name}").status_code == 404
    assert c.delete("/api/v1/live/watch/program/whep/x", headers={"X-CSRF": c.csrf}).status_code == 404
    assert relay == []


def test_other_mosque_only_reaches_its_own_stream(env, relay):
    conn = env["conn"]
    mid2 = db.create_mosque(conn, "autre-mosquee", "Other mosque")
    auth.create_user(conn, mid2, "admin2", PW, "admin")
    other = client(env, "admin2")
    assert _offer(other).status_code == 201
    assert other.get("/api/v1/live/watch/program/hls/index.m3u8").status_code == 200
    paths = {p for _m, _port, p, _c in relay}
    assert all("autre-mosquee" in p for p in paths), paths
    assert not any(_slug(env) in p for p in paths)


def test_hls_passes_the_relay_session(env, relay):
    c = client(env, "imam")
    r = c.get("/api/v1/live/watch/program/hls/index.m3u8")
    assert r.status_code == 200 and "main_stream.m3u8?session=5c9798b1-bac6" in r.text
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert relay[-1][3] == {"cookieCheck": "1"}
    assert c.get("/api/v1/live/watch/program/hls/main_stream.m3u8?session=5c9798b1-bac6").status_code == 200
    assert relay[-1][3] == {"session": "5c9798b1-bac6"}
    # anything else than a session id is dropped, never forwarded
    assert c.get("/api/v1/live/watch/program/hls/main_stream.m3u8?session=../x&path=live/autre").status_code == 200
    assert relay[-1][3] == {}


def test_regie_state_and_health(env, relay):
    c = client(env, "imam")
    d = c.get("/api/v1/live/regie").json()
    assert d["live"]["state"] == "IDLE"
    assert [x["key"] for x in d["commands"]] == list(live.COMMANDS)
    assert {x["key"] for x in d["commands"] if x["destructive"]} == {"stop", "cancel"}
    assert d["monitors"]["program"] == {"available": True, "health": {"online": True, "readers": 2, "kbps": None}}


def test_health_omitted_when_relay_has_no_api(monkeypatch):
    def down(*a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(live_watch, "_api_down_at", 0.0)
    monkeypatch.setattr(live_watch.httpx, "get", down)
    assert live_watch.health("live/x-prog") is None
    assert live_watch.health(None) == {"online": False}


def test_relay_api_reserved_to_the_back_office(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INTERNAL_TOKEN_FILE", str(tmp_path / "internal.token"))
    c = client(env)

    def ask(**q):
        return c.post("/api/v1/internal/publish-auth", json={"action": "api", "path": "", **q}).status_code

    good = {"user": live_watch.API_USER, "password": live_watch.api_password()}
    assert ask(ip="10.42.0.7", **good) == 200
    assert ask(ip="8.8.8.8", **good) == 401
    assert ask(ip="10.42.0.7", user=live_watch.API_USER, password="guess") == 401
    assert ask(ip="10.42.0.7") == 401


def test_camera_on_local_relay_and_remote_camera(env, monkeypatch):
    mosque = one(env["conn"], models.Mosque, id=env["mid"])
    monkeypatch.setattr(
        live_watch.live_input, "current", lambda conn, m: {"mode": "camera", "url": "rtsp://127.0.0.1:8554/cam"}
    )
    assert live_watch.path_of(env["conn"], mosque, "source") == "cam"
    monkeypatch.setattr(
        live_watch.live_input,
        "current",
        lambda conn, m: {"mode": "camera", "url": "rtsp://user:pw@192.168.1.20:554/h264"},
    )
    assert live_watch.path_of(env["conn"], mosque, "source") is None  # never dialled by the proxy


def test_scene_preview_for_studio_mode(env, relay):
    """Studio mode: a scene is drawn before going on air; banners come transparent, over the feed."""
    import io

    from PIL import Image

    c = client(env, "imam")
    r = c.get("/api/v1/live/regie/scene/priere.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).mode == "RGB"
    banner = Image.open(io.BytesIO(c.get("/api/v1/live/regie/scene/khutba.png").content))
    assert banner.mode == "RGBA" and banner.getpixel((0, 0))[3] == 0
    assert c.get("/api/v1/live/regie/scene/janaza.png").status_code == 404
    assert c.get("/api/v1/live/regie/scene/nope.png").status_code == 404
    assert client(env).get("/api/v1/live/regie/scene/priere.png").status_code == 401
