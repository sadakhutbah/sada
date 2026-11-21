"""Studio devices: pairing, credentials, liveness.

**Pairing** (the television way, RFC 8628 in spirit). The device asks for a code (`start`), shows the short
`user_code` and polls (`poll`) with its secret `device_code`. A signed-in person with the `live` right
(administrator or manager) types the code in the console, which attaches the device to *their* mosque
(`approve`). The pairing is a small state machine::

    pending --approve--> approved --first poll--> claimed
       |                                              (credential given once)
       +--10 minutes--> expired

Only hashes of the two codes are stored. The durable credential is `sdev_<id>.<secret>`: the secret is made at
the first poll after approval, shown once, and stored hashed. It opens the broadcast of that one mosque
(studio token, WHIP, heartbeat, live start/stop) and nothing of the back office: every route that accepts it
asks for it explicitly (`authenticate`).

A credential given but never used can be fetched again by the device (lost answer); once the device has spoken,
it cannot. Rotation: the console asks, the next heartbeat brings a new secret, the old one stays valid for a
grace period until the device uses the new one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

from sqlalchemy import select, update

from ..i18n import _
from . import db, orm
from .context import DeviceRefused
from .models import Device, DevicePairing, Mosque, User

PAIR_TTL_S = 600
POLL_INTERVAL_S = 5
REISSUE_WINDOW_S = 3600  # an unused credential can be fetched again for this long
ROTATE_GRACE_S = 3600
HOLD_S = 900  # a device displaced by an administrator stays quiet this long
PREFIX = "sdev_"
# No vowels, no 0/O/1/I/L: nothing to confuse when read from a screen and typed on another.
CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXZ23456789"
CODE_LEN = 8
_BEARER = re.compile(r"^Bearer\s+(sdev_(\d{1,9})\.([A-Za-z0-9_-]{20,100}))\s*$")


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ------------------------------------------------------------------ codes


def new_user_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for __ in range(CODE_LEN))


def show_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_code(text: str) -> str | None:
    """What a person typed ("abcd-2345", " ABCD 2345 ") -> canonical code, or None when it cannot be one."""
    c = re.sub(r"[\s-]", "", str(text or "")).upper()
    return c if len(c) == CODE_LEN and all(ch in CODE_ALPHABET for ch in c) else None


# ------------------------------------------------------------------ pairing


def start_pairing(conn, ip: str | None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    conn.execute(DevicePairing.__table__.delete().where(DevicePairing.expires_at < now - 86400))
    device_code = secrets.token_urlsafe(32)
    user_code = new_user_code()
    for _try in range(5):
        # two pending pairings must never share a code, or the console could not tell which one it approves
        clash = conn.scalar(
            select(DevicePairing.id).where(
                DevicePairing.user_code_hash == _h(user_code),
                DevicePairing.status == "pending",
                DevicePairing.expires_at > now,
            )
        )
        if not clash:
            break
        user_code = new_user_code()
    conn.add(
        DevicePairing(
            device_code_hash=_h(device_code),
            user_code_hash=_h(user_code),
            status="pending",
            created_at=now,
            expires_at=now + PAIR_TTL_S,
            ip=ip,
        )
    )
    orm.done(conn)
    return {
        "device_code": device_code,
        "user_code": show_code(user_code),
        "expires_in": PAIR_TTL_S,
        "interval": POLL_INTERVAL_S,
    }


class PairingError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def approve(conn, mosque: dict, user: dict, user_code: str, name: str, now: float | None = None) -> dict:
    """Attaches the pending pairing named by `user_code` to `mosque`: creates the device (no secret yet)."""
    now = time.time() if now is None else now
    code = normalize_code(user_code)
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise PairingError(422, "name_required", _("Give the device a name (for example “Mosque PC”)."))
    if code is None:
        raise PairingError(404, "unknown_code", _("This code does not exist. Check the code shown on the device."))
    rows = orm.rows(
        conn,
        select(DevicePairing)
        .where(DevicePairing.user_code_hash == _h(code))
        .order_by(DevicePairing.created_at.desc())
        .limit(1),
    )
    p = rows[0] if rows else None
    if p is None:
        raise PairingError(404, "unknown_code", _("This code does not exist. Check the code shown on the device."))
    if p["status"] != "pending":
        raise PairingError(409, "already_used", _("This code has already been used."))
    if p["expires_at"] <= now:
        raise PairingError(
            410, "expired", _("This code has expired (10 minutes). Restart the pairing on the device to get a new one.")
        )
    if device_name_taken(conn, mosque["id"], name):
        raise PairingError(409, "name_taken", _("A device of this mosque already has this name."))
    # the status test is part of the update: two simultaneous approvals cannot both win
    won = conn.execute(
        update(DevicePairing)
        .where(DevicePairing.id == p["id"], DevicePairing.status == "pending")
        .values(status="approved", mosque_id=mosque["id"], approved_by=user["id"], approved_at=now)
    ).rowcount
    if won != 1:
        orm.done(conn)
        raise PairingError(409, "already_used", _("This code has already been used."))
    dev = Device(mosque_id=mosque["id"], name=name, created_by=user["id"], created_at=db.now_iso())
    conn.add(dev)
    conn.flush()
    conn.execute(update(DevicePairing).where(DevicePairing.id == p["id"]).values(device_id=dev.id))
    orm.done(conn)
    return {"id": dev.id, "name": name}


def poll(conn, device_code: str, now: float | None = None) -> tuple[int, dict]:
    """(HTTP status, body) for the device's poll."""
    now = time.time() if now is None else now
    rows = orm.rows(conn, select(DevicePairing).where(DevicePairing.device_code_hash == _h(str(device_code or ""))))
    p = rows[0] if rows else None
    if p is None:
        return 404, {"status": "unknown", "message": _("Unknown pairing: start again.")}
    if p["status"] == "pending":
        if p["expires_at"] <= now:
            return 410, {"status": "expired", "message": _("The code has expired: start the pairing again.")}
        return 200, {"status": "pending", "interval": POLL_INTERVAL_S, "expires_in": int(p["expires_at"] - now)}
    dev = orm.first(conn, Device, id=p["device_id"])
    if dev is None or dev["revoked_at"]:
        return 410, {"status": "revoked", "message": _("This device has been revoked.")}
    if p["status"] == "claimed":
        # answer lost on the way: the credential can be fetched again as long as the device never spoke
        if dev["last_seen_at"] is not None or now - (p["claimed_at"] or 0) > REISSUE_WINDOW_S:
            return 410, {"status": "claimed", "message": _("This pairing was already completed.")}
    else:
        won = conn.execute(
            update(DevicePairing)
            .where(DevicePairing.id == p["id"], DevicePairing.status == "approved")
            .values(status="claimed", claimed_at=now)
        ).rowcount
        orm.done(conn)
        if won != 1:
            return 409, {"status": "claimed", "message": _("This pairing was already completed.")}
    secret = new_secret()
    conn.execute(update(Device).where(Device.id == dev["id"]).values(secret_hash=_h(secret)))
    orm.done(conn)
    mosque = orm.first(conn, Mosque, id=dev["mosque_id"]) or {}
    return 200, {
        "status": "approved",
        "device_id": credential(dev["id"], secret),
        "name": dev["name"],
        "mosque": {"name": mosque.get("name", ""), "slug": mosque.get("slug", "")},
    }


# ------------------------------------------------------------------ credentials


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def credential(device_id: int, secret: str) -> str:
    return f"{PREFIX}{device_id}.{secret}"


def authenticate(conn, header: str | None, ip: str | None = None, now: float | None = None) -> dict:
    """The device named by an `Authorization: Bearer sdev_…` header. Raises `DeviceRefused` otherwise.

    A revoked device is refused here, on the very next call. The device row is returned with `rotated`
    (True when it just used the new secret of a rotation)."""
    now = time.time() if now is None else now
    m = _BEARER.match(header or "")
    if not m:
        raise DeviceRefused("invalid", _("device credential missing or malformed"))
    dev = orm.first(conn, Device, id=int(m.group(2)))
    given = _h(m.group(3))
    if dev is None or not dev["secret_hash"]:
        raise DeviceRefused("invalid", _("device credential unknown"))
    current = hmac.compare_digest(dev["secret_hash"], given)
    previous = (
        bool(dev["prev_secret_hash"])
        and now < dev["prev_valid_until"]
        and hmac.compare_digest(dev["prev_secret_hash"], given)
    )
    if not (current or previous):
        raise DeviceRefused("invalid", _("device credential unknown"))
    if dev["revoked_at"]:
        raise DeviceRefused("revoked", _("this device has been revoked by the mosque"))
    values: dict = {"last_seen_at": now}
    if ip:
        values["last_ip"] = ip
    if current and dev["prev_secret_hash"]:
        values.update(prev_secret_hash="", prev_valid_until=0.0, rotate_requested=0)  # new secret adopted
    conn.execute(update(Device).where(Device.id == dev["id"]).values(**values))
    orm.done(conn)
    dev.update(values)
    dev["via_previous"] = previous and not current
    return dev


def rotate_if_requested(conn, dev: dict, now: float | None = None) -> str | None:
    """New credential to hand to a device whose rotation was requested (None otherwise)."""
    now = time.time() if now is None else now
    if not dev["rotate_requested"]:
        return None
    secret = new_secret()
    keep = dev["prev_secret_hash"] if dev.get("via_previous") else dev["secret_hash"]
    conn.execute(
        update(Device)
        .where(Device.id == dev["id"])
        .values(secret_hash=_h(secret), prev_secret_hash=keep, prev_valid_until=now + ROTATE_GRACE_S)
    )
    orm.done(conn)
    return credential(dev["id"], secret)


def request_rotation(conn, device_id: int) -> None:
    conn.execute(update(Device).where(Device.id == device_id).values(rotate_requested=1))
    orm.done(conn)


def revoke(conn, dev: dict, user_id: int, now: float | None = None) -> None:
    conn.execute(
        update(Device).where(Device.id == dev["id"]).values(revoked_at=db.now_iso(), revoked_by=user_id, held_until=0.0)
    )
    orm.done(conn)


def device_name_taken(conn, mosque_id: int, name: str, except_id: int | None = None) -> bool:
    for d in orm.rows(conn, select(Device).where(Device.mosque_id == mosque_id, Device.revoked_at.is_(None))):
        if d["id"] != except_id and d["name"].casefold() == name.casefold():
            return True
    return False


# ------------------------------------------------------------------ liveness


def record_heartbeat(conn, dev: dict, body: dict, now: float | None = None) -> None:
    now = time.time() if now is None else now

    def flag(v):
        return None if v is None else int(bool(v))

    def kbps(v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return None

    health = body.get("health")
    clean_health = (
        {k: bool(v) for k, v in health.items() if isinstance(k, str) and len(k) < 40}
        if isinstance(health, dict)
        else {}
    )
    conn.execute(
        update(Device)
        .where(Device.id == dev["id"])
        .values(
            last_seen_at=now,
            app_version=str(body.get("version") or "")[:40],
            last_state=str(body.get("state") or "")[:20],
            camera_ok=flag(body.get("camera")),
            mic_ok=flag(body.get("microphone")),
            bitrate_kbps=kbps(body.get("bitrate_kbps")),
            health=json.dumps(clean_health),
        )
    )
    orm.done(conn)


def is_online(dev: dict, now: float, online_s: int = 90) -> bool:
    return not dev["revoked_at"] and dev["last_seen_at"] is not None and now - dev["last_seen_at"] <= online_s


def health_of(dev: dict) -> dict:
    try:
        d = json.loads(dev.get("health") or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def public(dev: dict, now: float, online_s: int = 90) -> dict:
    """A device as the console shows it (never a secret)."""
    return {
        "id": dev["id"],
        "name": dev["name"],
        "online": is_online(dev, now, online_s),
        "revoked": bool(dev["revoked_at"]),
        "revoked_at": dev["revoked_at"],
        "created_at": dev["created_at"],
        "created_by": dev["created_by"],
        "last_seen_at": dev["last_seen_at"],
        "app_version": dev["app_version"],
        "state": dev["last_state"],
        "camera": None if dev["camera_ok"] is None else bool(dev["camera_ok"]),
        "microphone": None if dev["mic_ok"] is None else bool(dev["mic_ok"]),
        "bitrate_kbps": dev["bitrate_kbps"],
        "health": health_of(dev),
        "held": dev["held_until"] > now,
        "rotation_pending": bool(dev["rotate_requested"]),
    }


def creator_names(conn, devices: list[dict]) -> dict[int, str]:
    ids = {d["created_by"] for d in devices if d["created_by"]}
    if not ids:
        return {}
    return {
        r["id"]: r["display_name"] or r["username"]
        for r in orm.rows(conn, select(User.id, User.display_name, User.username).where(User.id.in_(ids)))
    }
