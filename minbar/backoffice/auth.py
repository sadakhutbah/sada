"""Comptes, mots de passe (argon2id), sessions serveur, jeton CSRF, limitation des tentatives, droits."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import db

ROLES = ("admin", "responsable", "imam")
ROLE_LABELS = {"admin": "Administrateur", "responsable": "Responsable", "imam": "Imam"}

# Matrice des droits : une seule source de vérité, testée (tests/test_backoffice.py).
#   live        : piloter le live (live.COMMANDS) — SPEC 5.0 ter : imam et responsables
#   history_all : voir toutes les khutbas de la mosquée (l'imam ne voit que les siennes)
#   validate    : corriger / valider / rejeter un clip (l'imam : seulement les clips de ses khutbas)
#   publish     : publier un clip validé
#   assign      : rattacher une khutba à un imam, lancer un import
#   journal     : consulter le journal
#   admin       : comptes, configuration de la mosquée, connecteurs
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
SESSION_TTL = 7 * 24 * 3600          # durée maximale d'une session
IDLE_TTL = 12 * 3600                 # déconnexion après 12 h d'inactivité
COOKIE = "minbar_session"

_ph = PasswordHasher()               # argon2id, paramètres par défaut de argon2-cffi (RFC 9106 « low memory »)


def can(user: dict | None, perm: str) -> bool:
    return bool(user) and user["role"] in PERMS[perm]


def hash_password(pw: str) -> str:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"mot de passe trop court ({MIN_PASSWORD} caractères minimum)")
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
    """Imams actifs de la mosquée (choix de l'imam d'une khutba, imam par défaut)."""
    return db.all_(conn, "SELECT id, display_name FROM users WHERE mosque_id = ? AND role = 'imam' AND active = 1 "
                         "ORDER BY display_name", mosque_id)


def imam_id(conn, mosque_id: int, value) -> int | None:
    """Identifiant d'imam actif de la mosquée lu dans un formulaire, sinon None."""
    iid = int(value) if str(value or "").isdigit() else None
    return iid if iid in {u["id"] for u in imams(conn, mosque_id)} else None


def create_user(conn, mosque_id: int, username: str, password: str, role: str, display_name: str = "") -> int:
    username = username.strip()
    if role not in ROLES:
        raise ValueError(f"rôle inconnu : {role}")
    if not username or len(username) > 64 or any(c.isspace() for c in username):
        raise ValueError("identifiant invalide (sans espace, 64 caractères max)")
    if db.one(conn, "SELECT id FROM users WHERE username = ?", username):
        raise ValueError(f"l'identifiant « {username} » existe déjà")
    cur = conn.execute("INSERT INTO users (mosque_id, username, display_name, password_hash, role, created_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (mosque_id, username, display_name.strip() or username, hash_password(password), role, db.now_iso()))
    return cur.lastrowid


def set_password(conn, user_id: int, password: str) -> None:
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))   # déconnecte partout


def authenticate(conn, username: str, password: str) -> dict | None:
    u = db.one(conn, "SELECT * FROM users WHERE username = ?", username.strip())
    if not u:
        verify_password(_dummy_hash(), password)   # temps constant : ne révèle pas si le compte existe
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
    """Empreinte courte du navigateur : une session volée depuis un autre navigateur est refusée.

    Volontairement tolérante (pas la version complète du user agent, qui change à chaque mise à jour) :
    on retient la famille et le système.
    """
    ua = ua or ""
    fam = next((f for f in ("Firefox", "Edg", "Chrome", "Safari", "OPR") if f in ua), "autre")
    sys_ = next((f for f in ("Windows", "Android", "iPhone", "iPad", "Mac OS", "Linux") if f in ua), "")
    return _h(f"{fam}|{sys_}")[:16]


def open_session(conn, user_id: int, ip: str | None, ua: str | None) -> str:
    """Crée une session ; renvoie le jeton (seul son empreinte SHA-256 est stockée)."""
    token = secrets.token_urlsafe(32)
    t = time.time()
    conn.execute("INSERT INTO auth_sessions (token_hash, user_id, csrf, created_at, expires_at, last_seen_at, ip, user_agent) "
                 "VALUES (?,?,?,?,?,?,?,?)", (_h(token), user_id, secrets.token_urlsafe(24), t, t + SESSION_TTL, t, ip,
                                              ua_tag(ua)))
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (db.now_iso(), user_id))
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ? OR last_seen_at < ?", (t, t - IDLE_TTL))
    return token


def load_session(conn, token: str | None, ua: str | None = None, ip: str | None = None) -> tuple[dict, dict] | None:
    """-> (session, utilisateur) si la session est valide, sinon None.

    Rejetée si expirée, inactive trop longtemps, ou présentée par un autre navigateur que celui de
    l'ouverture (empreinte ua_tag) : un jeton recopié ailleurs ne sert donc à rien.
    """
    if not token:
        return None
    s = db.one(conn, "SELECT * FROM auth_sessions WHERE token_hash = ?", _h(token))
    t = time.time()
    if not s:
        return None
    if (s["user_agent"] or "autre") != ua_tag(ua):
        # Jeton présenté par un autre navigateur : on NE détruit PAS la session (ce serait offrir au
        # voleur un déni de service sur le compte), on journalise pour que le Maître voie la tentative.
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


# --------------------------------------------------------- limitation des essais

class RateLimiter:
    """5 échecs en 10 min (par IP + identifiant, ou 20 par IP) -> blocage 10 min."""

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
            return len(self._recent(f"u:{ip}:{username.lower()}")) >= self.per_user or len(self._recent(f"ip:{ip}")) >= self.per_ip

    def fail(self, ip: str, username: str) -> None:
        with self._lock:
            t = time.time()
            for k in (f"u:{ip}:{username.lower()}", f"ip:{ip}"):
                self._recent(k).append(t)

    def success(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop(f"u:{ip}:{username.lower()}", None)
