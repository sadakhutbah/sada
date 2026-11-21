"""Monitors of the control room: the mosque's raw feed and the programme, read through the back office.

The browser never talks to the relay's HTTP side: the WebRTC signalling (WHEP) and the HLS fallback
are proxied here, behind the session and the `live` permission. The relay path is derived from the
mosque the session is open on, never from the request, so a session only ever watches its own
mosque. The relay authorises these reads because they come from the back office (private network).

The WebRTC media itself flows over UDP between the browser and the relay (ICE): that port is
published, but a media session only exists after an offer this module forwarded.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from urllib.parse import quote, urlsplit

import httpx

from . import internal, live_input

SOURCES = ("source", "program")
RELAY_HLS_PORT = 8888
RELAY_WEBRTC_PORT = 8889
RELAY_API_PORT = 9997
# Names the relay gives HLS files (index.m3u8, stream.m3u8, <id>_seg3.ts, init.mp4…): nothing else
# is forwarded, so the proxy can never be walked to another path.
HLS_FILE = re.compile(r"^[A-Za-z0-9_-]{1,80}\.(m3u8|ts|mp4|m4s)$")
WHEP_SESSION = re.compile(r"^[A-Za-z0-9-]{8,80}$")
_RELAY_PATH = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")
HLS_TYPES = {
    "m3u8": "application/vnd.apple.mpegurl",
    "ts": "video/mp2t",
    "mp4": "video/mp4",
    "m4s": "video/iso.segment",
}


# The relay's API (stream health) answers the back office only: it presents these credentials, the
# relay asks /internal/publish-auth, which recomputes them from the back office's local secret.
API_USER = "sada-backoffice"


def api_password() -> str:
    return hmac.new(internal.local_token().encode(), b"relay-api", hashlib.sha256).hexdigest()


def api_allowed(user, password) -> bool:
    return user == API_USER and hmac.compare_digest(str(password or ""), api_password())


class RelayUnavailable(Exception):
    """The relay does not answer (not started, or another network)."""


def relay_host() -> str:
    return os.environ.get(live_input.RELAY_HOST_ENV, "").strip() or "127.0.0.1"


def path_of(conn, mosque, which: str) -> str | None:
    """Relay path of a monitor, or None when this mosque's feed does not go through the relay.

    `program` is the monitoring copy the orchestrator publishes; `source` is what the mosque
    pushes (or, on a self-hosted install, a camera already served by the local relay)."""
    name = live_input.slug(mosque)
    if which == "program":
        return f"live/{name}-prog"
    if which != "source":
        return None
    cur = live_input.current(conn, mosque)
    if cur["mode"] == "push":
        return f"live/{name}"
    parts = urlsplit(cur["url"] or "")
    local = parts.hostname in {relay_host(), "127.0.0.1", "localhost"}
    path = parts.path.strip("/")
    if local and (parts.port or 554) == live_input.RELAY_RTSP_PORT and _RELAY_PATH.match(path):
        return path
    return None


def _url(port: int, tail: str) -> str:
    return f"http://{relay_host()}:{port}/{tail}"


async def _send(method: str, url: str, **kw) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=kw.pop("timeout", 8.0)) as client:
            return await client.request(method, url, **kw)
    except httpx.HTTPError as e:
        raise RelayUnavailable(str(e)) from e


async def whep_offer(path: str, sdp: bytes) -> tuple[int, bytes, str | None]:
    """Forwards an SDP offer: (status, SDP answer, relay session id)."""
    r = await _send(
        "POST", _url(RELAY_WEBRTC_PORT, f"{path}/whep"), content=sdp, headers={"Content-Type": "application/sdp"}
    )
    session = None
    if r.status_code == 201:
        session = (r.headers.get("location") or "").rstrip("/").rsplit("/", 1)[-1] or None
        if session and not WHEP_SESSION.match(session):
            session = None
    return r.status_code, r.content, session


async def whep_close(path: str, session: str) -> int:
    r = await _send("DELETE", _url(RELAY_WEBRTC_PORT, f"{path}/whep/{session}"))
    return r.status_code


async def whip_request(
    method: str, path: str, token: str, session: str | None = None, body: bytes = b"", ctype: str | None = None
) -> httpx.Response:
    """One WHIP call to the relay (offer, ICE patch, close), authenticated with a studio token.

    The token goes as a bearer: the relay hands it to /internal/publish-auth (protocol webrtc)."""
    tail = f"{path}/whip" + (f"/{session}" if session else "")
    headers = {"Authorization": f"Bearer {token}"}
    if ctype:
        headers["Content-Type"] = ctype
    return await _send(method, _url(RELAY_WEBRTC_PORT, tail), content=body or None, headers=headers)


HLS_SESSION = re.compile(r"^[A-Za-z0-9-]{1,64}$")


async def hls_file(path: str, name: str, session: str | None = None) -> tuple[int, bytes, str]:
    """One HLS file of the relay: (status, body, content type).

    The relay ties each reader to a session. Asked without cookies (cookieCheck answered up front),
    it carries the session in the playlist URLs (`?session=…`), which the player sends back
    through the proxy: no cookie to relay, the playlists can be passed on untouched."""
    params = {"session": session} if session and HLS_SESSION.match(session) else {}
    if name == "index.m3u8":
        params["cookieCheck"] = "1"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(_url(RELAY_HLS_PORT, f"{path}/{name}"), params=params)
    except httpx.HTTPError as e:
        raise RelayUnavailable(str(e)) from e
    return r.status_code, r.content, HLS_TYPES[name.rsplit(".", 1)[-1]]


# --- Stream health, from the relay's API (bitrate needs two samples: kept per path)

_samples: dict[str, tuple[float, int]] = {}
_lock = threading.Lock()
# A relay without API (or unreachable) is not asked again before this delay: the control room polls
# every few seconds and must not wait on a timeout each time.
API_RETRY_S = 30.0
_api_down_at = 0.0


def health(path: str | None, timeout: float = 1.0) -> dict | None:
    """{online, readers, kbps} of a path; None when the relay exposes no API."""
    global _api_down_at
    if not path:
        return {"online": False}
    if time.monotonic() - _api_down_at < API_RETRY_S:
        return None
    try:
        r = httpx.get(
            _url(RELAY_API_PORT, f"v3/paths/get/{quote(path, safe='')}"),
            auth=(API_USER, api_password()),
            timeout=timeout,
        )
    except httpx.HTTPError:
        _api_down_at = time.monotonic()
        return None
    if r.status_code == 404:
        return {"online": False}
    if r.status_code != 200:
        return None
    try:
        info = r.json()
    except ValueError:
        return None
    now, total = time.monotonic(), int(info.get("inboundBytes") or info.get("bytesReceived") or 0)
    with _lock:
        before = _samples.get(path)
        _samples[path] = (now, total)
    kbps = None
    if before and now > before[0] and total >= before[1]:
        kbps = round((total - before[1]) * 8 / 1000 / (now - before[0]))
    return {
        "online": bool(info.get("ready")),
        "readers": len(info.get("readers") or []),
        "kbps": kbps,
    }


# --- Who publishes on a path, and cutting them off (exclusivity, revocation, takeover)

# MediaMTX names the publisher's kind in the path's `source`; each kind is kicked on its own API route.
KICK_ROUTES = {
    "webRTCSession": "webrtcsessions",
    "rtmpConn": "rtmpconns",
    "rtmpsConn": "rtmpconns",
    "srtConn": "srtconns",
    "rtspSession": "rtspsessions",
    "rtspsSession": "rtspssessions",
}


def publisher_info(path: str, timeout: float = 1.0) -> dict | None:
    """{ready, type, id} of the path's publisher; None when the relay's API does not answer (unknown)."""
    global _api_down_at
    if time.monotonic() - _api_down_at < API_RETRY_S:
        return None
    try:
        r = httpx.get(
            _url(RELAY_API_PORT, f"v3/paths/get/{quote(path, safe='')}"),
            auth=(API_USER, api_password()),
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return None  # slow, not down: the relay can wait for an authorisation of this very path (see kick/takeover)
    except httpx.HTTPError:
        _api_down_at = time.monotonic()
        return None
    if r.status_code == 404:
        return {"ready": False, "type": "", "id": ""}
    if r.status_code != 200:
        return None
    try:
        info = r.json()
    except ValueError:
        return None
    src = info.get("source") or {}
    return {"ready": bool(info.get("ready")), "type": src.get("type") or "", "id": src.get("id") or ""}


def kick(path: str, timeout: float = 2.0) -> bool:
    """Cuts the path's current publisher through the relay's API. True when a session was kicked."""
    info = publisher_info(path, timeout)
    route = KICK_ROUTES.get((info or {}).get("type", ""))
    if not info or not info["ready"] or not route or not WHEP_SESSION.match(info["id"] or ""):
        return False
    try:
        r = httpx.post(
            _url(RELAY_API_PORT, f"v3/{route}/kick/{info['id']}"), auth=(API_USER, api_password()), timeout=timeout
        )
    except httpx.HTTPError:
        return False
    return r.status_code == 200
