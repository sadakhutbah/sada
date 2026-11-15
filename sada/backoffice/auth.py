"""Accounts, passwords (argon2id), server sessions, CSRF token, attempt limiting, permissions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, func, or_, select, update

from ..i18n import _
from . import db
from .models import AuthSession, Membership, Mosque, User
from .orm import as_dict


def _same_name(username: str):
    """Usernames compare without case (SQLite NOCASE column, same rule on PostgreSQL)."""
    return func.lower(User.username) == username.lower()


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


#: Permission of the platform administrator, outside PERMS: no form can grant it.
PLATFORM = "platform"


def permissions(user: dict) -> list[str]:
    custom = custom_permissions(user)
    perms = role_permissions(user["role"]) if custom is None else custom
    return [*perms, PLATFORM] if user.get("platform_admin") else perms


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


def hosted() -> bool:
    """Hosted mode (SADA_MODE=hosted): signing in never opens a mosque by itself."""
    return os.environ.get("SADA_MODE", "").strip().lower() == "hosted"


def mosques_of(conn, user: dict) -> list[dict]:
    """Mosques a person can open: the home one plus the extra memberships."""
    ids = {user["mosque_id"], *conn.scalars(select(Membership.mosque_id).where(Membership.user_id == user["id"]))}
    rows = conn.execute(select(Mosque.id, Mosque.slug, Mosque.name).where(Mosque.id.in_(ids)).order_by(Mosque.name))
    return [dict(r._mapping) for r in rows]


def all_mosques(conn) -> list[dict]:
    """Every mosque of the platform (operator console)."""
    rows = conn.execute(select(Mosque.id, Mosque.slug, Mosque.name).order_by(Mosque.name))
    return [dict(r._mapping) for r in rows]


def choose_mosque(conn, session: dict, user: dict, mosque_id: int) -> str | None:
    """Opens `mosque_id` on the session. Returns None if refused, 'member' or 'support'."""
    if user.get("platform_admin") and hosted():
        # The operator is not a mosque administrator: on a hosted platform every entry is support.
        kind = "support" if conn.scalar(select(Mosque.id).where(Mosque.id == mosque_id)) is not None else None
        if kind is None:
            return None
    elif mosque_id in {m["id"] for m in mosques_of(conn, user)}:
        kind = "member"
    elif user.get("platform_admin") and conn.scalar(select(Mosque.id).where(Mosque.id == mosque_id)) is not None:
        kind = "support"
    else:
        return None
    conn.execute(
        update(AuthSession)
        .where(AuthSession.id == session["id"])
        .values(mosque_id=mosque_id, support=1 if kind == "support" else 0)
    )
    conn.commit()
    return kind


def leave_mosque(conn, session: dict) -> None:
    conn.execute(update(AuthSession).where(AuthSession.id == session["id"]).values(mosque_id=None, support=0))
    conn.commit()


def imams(conn, mosque_id: int) -> list[dict]:
    """Active imams of the mosque (choice of the imam of a khutba, default imam)."""
    q = (
        select(User.id, User.display_name)
        .where(User.mosque_id == mosque_id, User.role == "imam", User.active == 1)
        .order_by(User.display_name)
    )
    return [dict(r._mapping) for r in conn.execute(q)]


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
    if conn.scalar(select(User.id).where(_same_name(username))) is not None:
        raise ValueError(_("the username “{username}” already exists", username=username))
    u = User(
        mosque_id=mosque_id,
        username=username,
        display_name=display_name.strip() or username,
        password_hash=hash_password(password),
        role=role,
        permissions=normalize_permissions(perms),
        created_at=db.now_iso(),
    )
    conn.add(u)
    conn.commit()
    return u.id


def set_password(conn, user_id: int, password: str, keep_session: int | None = None) -> None:
    """Signs the account out everywhere, except `keep_session` (the person changing their own password)."""
    conn.execute(update(User).where(User.id == user_id).values(password_hash=hash_password(password)))
    conn.execute(
        delete(AuthSession).where(AuthSession.user_id == user_id, AuthSession.id.is_distinct_from(keep_session))
    )
    conn.commit()


def authenticate(conn, username: str, password: str) -> dict | None:
    u = as_dict(conn.scalar(select(User).where(_same_name(username.strip()))))
    if not u:
        verify_password(_dummy_hash(), password)  # constant time: does not reveal whether the account exists
        return None
    if not verify_password(u["password_hash"], password) or not u["active"]:
        return None
    if _ph.check_needs_rehash(u["password_hash"]):
        conn.execute(update(User).where(User.id == u["id"]).values(password_hash=_ph.hash(password)))
        conn.commit()
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
    conn.add(
        AuthSession(
            token_hash=_h(token),
            user_id=user_id,
            csrf=secrets.token_urlsafe(24),
            created_at=t,
            expires_at=t + SESSION_TTL,
            last_seen_at=t,
            ip=ip,
            user_agent=ua_tag(ua),
        )
    )
    conn.execute(update(User).where(User.id == user_id).values(last_login_at=db.now_iso()))
    conn.execute(delete(AuthSession).where(or_(AuthSession.expires_at < t, AuthSession.last_seen_at < t - IDLE_TTL)))
    conn.commit()
    return token


def load_session(conn, token: str | None, ua: str | None = None, ip: str | None = None) -> tuple[dict, dict] | None:
    """-> (session, user) if the session is valid, otherwise None.

    Rejected if expired, inactive for too long, or presented by a browser other than the one that
    opened it (ua_tag fingerprint): a token copied elsewhere is therefore useless.
    """
    if not token:
        return None
    s = as_dict(conn.scalar(select(AuthSession).where(AuthSession.token_hash == _h(token))))
    t = time.time()
    if not s:
        return None
    if (s["user_agent"] or "autre") != ua_tag(ua):
        # Token presented by another browser: we do NOT destroy the session (that would hand the
        # thief a denial of service on the account), we log it so the Master sees the attempt.
        db.audit(conn, None, s["user_id"], "session.navigateur_inattendu", "session", s["id"], ip=ip)
        return None
    if s["expires_at"] < t or s["last_seen_at"] < t - IDLE_TTL:
        conn.execute(delete(AuthSession).where(AuthSession.id == s["id"]))
        conn.commit()
        return None
    u = as_dict(conn.scalar(select(User).where(User.id == s["user_id"], User.active == 1)))
    if not u:
        return None
    if t - s["last_seen_at"] > 60:
        conn.execute(update(AuthSession).where(AuthSession.id == s["id"]).values(last_seen_at=t))
        conn.commit()
    return s, u


def close_session(conn, token: str | None) -> None:
    if token:
        conn.execute(delete(AuthSession).where(AuthSession.token_hash == _h(token)))
        conn.commit()


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
