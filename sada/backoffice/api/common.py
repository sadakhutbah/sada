"""Response shapes and read helpers shared by several API domains."""

from __future__ import annotations

from ... import notify
from .. import auth, branding, db
from ..context import Ctx

PERMISSIONS = tuple(auth.PERMS)


def _user(ctx: Ctx) -> dict:
    u = ctx.user
    return {
        "id": u["id"],
        "name": u["display_name"] or u["username"],
        "role": u["role"],
        "role_label": auth.role_label(u["role"]),
        "permissions": [p for p in PERMISSIONS if ctx.can(p)],
    }


def _mosque(ctx: Ctx) -> dict:
    m = ctx.mosque
    return {
        "id": m["id"],
        "slug": m["slug"],
        "name": m["name"],
        "timezone": m.get("timezone") or "",
        "logo": branding.url(m),
        "clip_title": m.get("clip_title") or "",
        # first login, or a mosque whose live was never set up: offer the three-step wizard once
        "needs_onboarding": not (m.get("onboarded_at") or "").strip(),
    }


def _ok(message: str, **extra) -> dict:
    return {"ok": True, "message": message, **extra}


def _notification_settings(ctx: Ctx) -> dict:
    """Notification settings for the imam; the webhook and Telegram token (secrets) are masked."""
    r = db.one(ctx.conn, "SELECT * FROM notifications WHERE mosque_id = ?", ctx.mosque["id"]) or {}
    n = notify.from_settings(r)
    return {
        "mode": r.get("mode", ""),
        "webhook": db.MASK if r.get("webhook") else "",
        "telegram_token": db.MASK if r.get("telegram_token") else "",
        "telegram_chat": r.get("telegram_chat", ""),
        "active": n.enabled,
        "destination": n.destination,
    }


def _a_table(conn, nom: str) -> bool:
    return bool(db.one(conn, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", nom))
