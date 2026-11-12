"""Live destinations: the addresses the encoder sends the stream to, configured from the back office.

A destination is a label and an RTMP address; the key is the last path segment of the address
(`rtmps://host/rtmp/<key>`). As everywhere else, a stored key never comes back in clear: the screen gets
a mask and sending the mask back means "unchanged". The orchestrator reads the enabled ones over the
internal endpoint and sends the same stream to all of them.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..i18n import _
from . import db

SCHEMES = ("rtmp://", "rtmps://")
MAX_TARGETS = 6
LABEL_MAX = 60


class InvalidTarget(ValueError):
    """A destination the screen sent cannot be used as is."""


def mask(url: str | None) -> str:
    """Keeps the server and the path prefix, hides the key (the last segment)."""
    url = (url or "").strip()
    base, sep, key = url.rpartition("/")
    if not sep or not key or base.endswith("/") or "://" not in base:
        return url
    return base + "/" + db.MASK


def label_for(url: str) -> str:
    """Readable name of a destination that was saved without one: the host."""
    host = urlsplit(url).hostname or url
    return host[:LABEL_MAX]


def check(url: str | None) -> tuple[str, str]:
    """(address trimmed, error message): the address must be a usable RTMP/RTMPS URL."""
    url = (url or "").strip()
    if not url:
        return "", _("The stream address is required.")
    if not url.startswith(SCHEMES):
        return url, _("The address must start with rtmp:// or rtmps://.")
    if re.search(r"\s", url):
        return url, _("The address must not contain a space.")
    parts = urlsplit(url)
    if not parts.hostname:
        return url, _("The stream address has no server.")
    if not parts.path.strip("/"):
        return url, _("The stream address has no key (last segment).")
    return url, ""


def rows(conn, mosque) -> list[dict]:
    """Destinations as the screen shows them: key masked, never in clear."""
    out = db.all_(conn, "SELECT * FROM live_targets WHERE mosque_id = ? ORDER BY id", mosque["id"])
    return [
        {
            "id": r["id"],
            "label": r["label"] or label_for(r["url"]),
            "url": mask(r["url"]),
            "enabled": bool(r["enabled"]),
        }
        for r in out
    ]


def effective(conn, mosque_id: int) -> list[str]:
    """Addresses in clear, for the encoder: the enabled destinations, in order."""
    return [
        r["url"]
        for r in db.all_(
            conn, "SELECT url FROM live_targets WHERE mosque_id = ? AND enabled = 1 ORDER BY id", mosque_id
        )
    ]


def _stored(conn, mosque_id: int, target_id) -> str:
    row = db.one(conn, "SELECT url FROM live_targets WHERE id = ? AND mosque_id = ?", target_id, mosque_id)
    return row["url"] if row else ""


def save(conn, mosque, targets, user) -> list[dict]:
    """Replaces the whole list in one gesture (the screen sends it complete)."""
    if not isinstance(targets, list):
        raise InvalidTarget(_("Unreadable destination list."))
    if len(targets) > MAX_TARGETS:
        raise InvalidTarget(_("{max_targets} destinations maximum.", max_targets=MAX_TARGETS))
    clean: list[dict] = []
    seen: set[str] = set()
    for i, t in enumerate(targets, start=1):
        t = t if isinstance(t, dict) else {}
        raw = str(t.get("url") or "").strip()
        before = _stored(conn, mosque["id"], t.get("id"))
        if before and raw == mask(before):
            url, err = before, ""  # the mask came back: the key is unchanged
        else:
            url, err = check(raw)
        if err:
            raise InvalidTarget(_("Destination {number}: {error}", number=i, error=err))
        if url in seen:
            raise InvalidTarget(_("Destination {number}: this address is already in the list.", number=i))
        seen.add(url)
        label = str(t.get("label") or "").strip()[:LABEL_MAX] or label_for(url)
        enabled = str(t.get("enabled", "1")).strip().lower() not in ("0", "false", "off", "non", "")
        clean.append({"id": t.get("id") or 0, "label": label, "url": url, "enabled": 1 if enabled else 0})

    keep = []
    for c in clean:
        if c["id"] and _stored(conn, mosque["id"], c["id"]):
            conn.execute(
                "UPDATE live_targets SET label = ?, url = ?, enabled = ?, updated_at = ? "
                "WHERE id = ? AND mosque_id = ?",
                (c["label"], c["url"], c["enabled"], db.now_iso(), c["id"], mosque["id"]),
            )
            keep.append(c["id"])
        else:
            keep.append(
                conn.execute(
                    "INSERT INTO live_targets (mosque_id, label, url, enabled, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (mosque["id"], c["label"], c["url"], c["enabled"], db.now_iso(), db.now_iso()),
                ).lastrowid
            )
    marks = ",".join("?" * len(keep)) if keep else "NULL"
    conn.execute(f"DELETE FROM live_targets WHERE mosque_id = ? AND id NOT IN ({marks})", (mosque["id"], *keep))
    db.audit(
        conn,
        mosque["id"],
        user["id"] if user else None,
        "direct.configure",
        "mosque",
        mosque["id"],
        destinations=[
            {"label": c["label"], "serveur": label_for(c["url"]), "actif": bool(c["enabled"])} for c in clean
        ],
    )
    return rows(conn, mosque)
