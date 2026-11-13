"""Accounts, passwords (argon2id), server sessions, CSRF token, attempt limiting, permissions."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..i18n import _
from . import db

ROLES = ("admin", "responsable", "imam")
# Source labels: the request's language is applied by `role_label` / `role_labels`.
ROLE_LABELS = {"admin": "Administrator", "responsable": "Manager", "imam": "Imam"}


def role_label(role: str) -> str:
    """Label of a role, in the request's language."""
    return _(ROLE_LABELS.get(role, role))


def role_labels() -> dict[str, str]:
    """Every role label, in the request's language."""
    return {r: role_label(r) for r in ROLE_LABELS}


# Permission matrix: a single source of truth, tested (tests/test_backoffice.py).
#   live        : drive the live (live.COMMANDS) — SPEC 5.0 ter: imam and responsables
#   history_all : see all the khutbas of the mosque (the imam only sees his own)
#   validate    : correct / validate / reject a clip (the imam: only the clips of his khutbas)
#   publish     : publish a validated clip
#   assign      : attach a khutba to an imam, start an import
#   journal     : consult the journal
#   admin       : accounts, mosque configuration, connectors
PERMS = {
    "live": {"admin", "responsable", "imam"},
    "history_all": {"admin", "responsable"},
    "validate": {"admin", "responsable", "imam"},
    "publish": {"admin", "responsable"},
    "assign": {"admin", "responsable"},
    "journal": {"admin", "responsable"},
    "admin": {"admin"},
}

MIN_PASSWORD = 10
SESSION_TTL = 7 * 24 * 3600  # maximum lifetime of a session
IDLE_TTL = 12 * 3600  # signed out after 12 h of inactivity
COOKIE = "sada_session"

_ph = PasswordHasher()  # argon2id, argon2-cffi default parameters (RFC 9106 "low memory")


#: Source labels of the permissions, translated by `perm_labels`.
PERM_LABELS = {
    "live": "Drive the live",
    "history_all": "See every session of the mosque",
    "validate": "Correct and validate clips",
    "publish": "Publish clips",
    "assign": "Assign sessions, import, dress videos",
    "journal": "Read the log",
    "admin": "Administration (accounts, settings, connectors)",
}


def perm_labels() -> dict[str, str]:
    return {p: _(PERM_LABELS[p]) for p in PERMS}


def role_permissions(role: str) -> list[str]:
    return [p for p in PERMS if role in PERMS[p]]


def custom_permissions(user: dict) -> list[str] | None:
    """Permissions set by hand on the account, or None when it follows its role."""
    raw = (user.get("permissions") or "").strip()
    if not raw:
        return None
    try:
        return [p for p in json.loads(raw) if p in PERMS]
    except (ValueError, TypeError):
        return None


def permissions(user: dict) -> list[str]:
    custom = custom_permissions(user)
    return role_permissions(user["role"]) if custom is None else custom


def normalize_permissions(value) -> str:
    """Form value -> stored column: '' (follow the role) or a sorted JSON list of known permissions."""
    if value in (None, "", "role"):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.lstrip().startswith("[") else [v for v in value.split(",") if v]
        except ValueError as e:
            raise ValueError(_("invalid permission list")) from e
    unknown = [p for p in value if p not in PERMS]
    if unknown:
        raise ValueError(_("unknown permission: {perm}", perm=unknown[0]))
    return json.dumps([p for p in PERMS if p in value])


def can(user: dict | None, perm: str) -> bool:
    return bool(user) and perm in permissions(user)


def hash_password(pw: str) -> str:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(_("password too short ({min_chars} characters minimum)", min_chars=MIN_PASSWORD))
    return _ph.hash(pw)


def verify_password(h: str, pw: str) -> bool:
    try:
        return _ph.verify(h, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


_DUMMY = None


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = _ph.hash(secrets.token_hex(16))
    return _DUMMY


def imams(conn, mosque_id: int) -> list[dict]:
    """Active imams of the mosque (choice of the imam of a khutba, default imam)."""
    return db.all_(
        conn,
        "SELECT id, display_name FROM users WHERE mosque_id = ? AND role = 'imam' AND active = 1 ORDER BY display_name",
        mosque_id,
    )


def imam_id(conn, mosque_id: int, value) -> int | None:
    """Id of an active imam of the mosque read from a form, otherwise None."""
    iid = int(value) if str(value or "").isdigit() else None
    return iid if iid in {u["id"] for u in imams(conn, mosque_id)} else None


def create_user(
    conn, mosque_id: int, username: str, password: str, role: str, display_name: str = "", perms=None
) -> int:
    username = username.strip()
    if role not in ROLES:
        raise ValueError(_("unknown role: {role}", role=role))
    if not username or len(username) > 64 or any(c.isspace() for c in username):
        raise ValueError(_("invalid username (no space, 64 characters max)"))
    if db.one(conn, "SELECT id FROM users WHERE username = ?", username):
        raise ValueError(_("the username “{username}” already exists", username=username))
    cur = conn.execute(
        "INSERT INTO users (mosque_id, username, display_name, password_hash, role, permissions, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            mosque_id,
            username,
            display_name.strip() or username,
            hash_password(password),
            role,
            normalize_permissions(perms),
            db.now_iso(),
        ),
    )
    return cur.lastrowid


def set_password(conn, user_id: int, password: str, keep_session: int | None = None) -> None:
    """Signs the account out everywhere, except `keep_session` (the person changing their own password)."""
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep_session))


def authenticate(conn, username: str, password: str) -> dict | None:
    u = db.one(conn, "SELECT * FROM users WHERE username = ?", username.strip())
    if not u:
        verify_password(_dummy_hash(), password)  # constant time: does not reveal whether the account exists
        return None
    if not verify_password(u["password_hash"], password) or not u["active"]:
        return None
    if _ph.check_needs_rehash(u["password_hash"]):
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_ph.hash(password), u["id"]))
    return u


# ------------------------------------------------------------------ sessions


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ua_tag(ua: str | None) -> str:
    """Short fingerprint of the browser: a session stolen from another browser is refused.

    Deliberately tolerant (not the full user agent version, which changes at every update):
    only the family and the system are kept.
    """
    ua = ua or ""
    fam = next((f for f in ("Firefox", "Edg", "Chrome", "Safari", "OPR") if f in ua), "autre")
    sys_ = next((f for f in ("Windows", "Android", "iPhone", "iPad", "Mac OS", "Linux") if f in ua), "")
    return _h(f"{fam}|{sys_}")[:16]


def open_session(conn, user_id: int, ip: str | None, ua: str | None) -> str:
    """Creates a session; returns the token (only its SHA-256 digest is stored)."""
    token = secrets.token_urlsafe(32)
    t = time.time()
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, csrf, created_at, expires_at, last_seen_at, ip, user_agent) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (_h(token), user_id, secrets.token_urlsafe(24), t, t + SESSION_TTL, t, ip, ua_tag(ua)),
    )
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (db.now_iso(), user_id))
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ? OR last_seen_at < ?", (t, t - IDLE_TTL))
    return token


def load_session(conn, token: str | None, ua: str | None = None, ip: str | None = None) -> tuple[dict, dict] | None:
    """-> (session, user) if the session is valid, otherwise None.

    Rejected if expired, inactive for too long, or presented by a browser other than the one that
    opened it (ua_tag fingerprint): a token copied elsewhere is therefore useless.
    """
    if not token:
        return None
    s = db.one(conn, "SELECT * FROM auth_sessions WHERE token_hash = ?", _h(token))
    t = time.time()
    if not s:
        return None
    if (s["user_agent"] or "autre") != ua_tag(ua):
        # Token presented by another browser: we do NOT destroy the session (that would hand the
        # thief a denial of service on the account), we log it so the Master sees the attempt.
        db.audit(conn, None, s["user_id"], "session.navigateur_inattendu", "session", s["id"], ip=ip)
        return None
    if s["expires_at"] < t or s["last_seen_at"] < t - IDLE_TTL:
        conn.execute("DELETE FROM auth_sessions WHERE id = ?", (s["id"],))
        return None
    u = db.one(conn, "SELECT * FROM users WHERE id = ? AND active = 1", s["user_id"])
    if not u:
        return None
    if t - s["last_seen_at"] > 60:
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (t, s["id"]))
    return s, u


def close_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_h(token),))


def check_csrf(session: dict, value: str | None) -> bool:
    return bool(value) and secrets.compare_digest(session["csrf"], str(value))


# --------------------------------------------------------- attempt limiting


class RateLimiter:
    """5 failures in 10 min (per IP + username, or 20 per IP) -> 10 min block."""

    def __init__(self, per_user: int = 5, per_ip: int = 20, window: float = 600.0):
        self.per_user, self.per_ip, self.window = per_user, per_ip, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        t = time.time()
        lst = [x for x in self._fails.get(key, []) if t - x < self.window]
        self._fails[key] = lst
        return lst

    def blocked(self, ip: str, username: str) -> bool:
        with self._lock:
            return (
                len(self._recent(f"u:{ip}:{username.lower()}")) >= self.per_user
                or len(self._recent(f"ip:{ip}")) >= self.per_ip
            )

    def fail(self, ip: str, username: str) -> None:
        with self._lock:
            t = time.time()
            for k in (f"u:{ip}:{username.lower()}", f"ip:{ip}"):
                self._recent(k).append(t)

    def success(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop(f"u:{ip}:{username.lower()}", None)
