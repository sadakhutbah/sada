"""Short-lived publish token of the broadcast studio (WHIP).

The studio never holds the mosque's permanent stream key. A signed-in person allowed to drive the
live (`live` right) asks the back office for a token bound to *their* account and *their* mosque; the
relay accepts it as a publish credential (`/internal/publish-auth`, action ``publish``, protocol
``webrtc``) on ``live/<slug>`` of that mosque only. The token is only meant to start a session and
every call of a session (offer, ICE, close) is relayed with a fresh one, so it lives a couple of
minutes.

Format: ``sdst1.<base64url(json)>.<base64url(hmac-sha256)>``. Claims: ``u`` user id, ``m`` mosque id,
``s`` relay stream name, ``exp`` expiry (epoch seconds).

Secret: ``SADA_STUDIO_TOKEN_SECRET`` when set (needed as soon as several back-office replicas verify
tokens), otherwise derived from the local internal token (``internal.local_token``) with a
purpose label, like the relay API password. Lifetime: ``SADA_STUDIO_TOKEN_TTL`` seconds (default
120, clamped to 30..600).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from . import internal, live_input

PREFIX = "sdst1"
SECRET_ENV = "SADA_STUDIO_TOKEN_SECRET"
TTL_ENV = "SADA_STUDIO_TOKEN_TTL"
DEFAULT_TTL = 120


def _secret() -> bytes:
    explicit = os.environ.get(SECRET_ENV, "").strip()
    if explicit:
        return explicit.encode()
    return hmac.new(internal.local_token().encode(), b"studio-whip-token", hashlib.sha256).digest()


def ttl() -> int:
    try:
        v = int(os.environ.get(TTL_ENV, "") or DEFAULT_TTL)
    except ValueError:
        v = DEFAULT_TTL
    return max(30, min(600, v))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_secret(), f"{PREFIX}.{body}".encode(), hashlib.sha256).digest())


def issue(user_id: int, mosque: dict, now: float | None = None) -> tuple[str, int]:
    """(token, lifetime in seconds) for this user on this mosque's relay path."""
    life = ttl()
    claims = {
        "u": int(user_id),
        "m": int(mosque["id"]),
        "s": live_input.slug(mosque),
        "exp": int((time.time() if now is None else now) + life),
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{PREFIX}.{body}.{_sign(body)}", life


def verify(token: str | None, slug: str, now: float | None = None) -> dict | None:
    """Claims when `token` is genuine, unexpired and issued for the relay stream `slug`; else None."""
    if not token or not slug:
        return None
    try:
        prefix, body, sig = str(token).split(".")
        if prefix != PREFIX or not hmac.compare_digest(sig, _sign(body)):
            return None
        claims = json.loads(_unb64(body))
        if not isinstance(claims, dict) or int(claims["exp"]) < (time.time() if now is None else now):
            return None
        if claims["s"] != slug or not isinstance(claims["u"], int) or not isinstance(claims["m"], int):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return claims
