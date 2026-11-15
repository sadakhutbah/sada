"""Entity states: one common shape, built next to the entity the page is about.

Every page that shows an entity (clip, khutba) receives a ``state`` object built here, so the
interface never guesses a state from scattered fields. The server sends stable keys only
(``code``, ``level``, action ``key``); the front end translates them (``states.<code>``).

Shape::

    {
      "level":   "ok" | "running" | "attention" | "failed",
      "code":    stable key of the situation (``clip.regenerating``, ``sermon.processing`` ...),
      "since":   ISO timestamp the situation started at, or None,
      "stuck":   True when a running situation has not ended after its threshold,
      "detail":  raw reason for a failure (shown only when it is plain text), or "",
      "actions": [{"key": "regenerate", "url": "/api/v1/..."}],
      "children": {"clips": {"running": 1, "failed": 0, "attention": 2}},   # optional
    }
"""

from __future__ import annotations

import datetime as dt

# A rendering is about a minute: past this, the user is told it looks stuck.
REGEN_STUCK_AFTER = dt.timedelta(minutes=10)
# Post-live processing (transcription, verses, clips) takes tens of minutes.
PROCESSING_STUCK_AFTER = dt.timedelta(hours=3)

OK, RUNNING, ATTENTION, FAILED = "ok", "running", "attention", "failed"


def _state(level: str, code: str, *, since=None, stuck=False, detail="", actions=None, children=None) -> dict:
    out = {
        "level": level,
        "code": code,
        "since": since or None,
        "stuck": bool(stuck),
        "detail": detail or "",
        "actions": actions or [],
    }
    if children:
        out["children"] = children
    return out


def _older_than(since: str | None, limit: dt.timedelta, now: dt.datetime | None = None) -> bool:
    if not since:
        return False
    try:
        start = dt.datetime.fromisoformat(since)
    except ValueError:
        return False
    if start.tzinfo is not None:
        start = start.astimezone().replace(tzinfo=None)
    return (now or dt.datetime.now()) - start > limit


def clip_state(clip: dict, *, can_regenerate: bool = False, now: dt.datetime | None = None) -> dict:
    """State of a clip's video: rebuilding, failed, outdated against the corrections, or fine."""
    regen = clip.get("regen_state") or ""
    action = (
        [{"key": "regenerate", "url": f"/api/v1/clips/{clip['id']}/regenerate"}] if can_regenerate else []
    )
    if regen == "en_cours":
        since = clip.get("updated_at")
        return _state(RUNNING, "clip.regenerating", since=since, stuck=_older_than(since, REGEN_STUCK_AFTER, now))
    if regen == "erreur":
        return _state(FAILED, "clip.regen_failed", since=clip.get("updated_at"), detail=clip.get("regen_error") or "", actions=action)
    if clip.get("needs_regen"):
        code = "clip.published_outdated" if clip.get("status") == "publie" else "clip.outdated"
        return _state(ATTENTION, code, since=clip.get("updated_at"), actions=action)
    return _state(OK, "clip.ready")


def clips_summary(states: list[dict]) -> dict:
    """Counts of the clips that are not fine, to show on the khutba without another request."""
    return {level: sum(1 for s in states if s["level"] == level) for level in (RUNNING, ATTENTION, FAILED)}


def sermon_state(processing: dict | None, clip_states: list[dict], *, now: dt.datetime | None = None) -> dict:
    """State of a khutba: post-live processing first, then what its clips need."""
    processing = processing or {}
    children = {"clips": clips_summary(clip_states)}
    phase = processing.get("etat")
    since = processing.get("debut")
    if phase == "attente":
        return _state(RUNNING, "sermon.queued", since=since, children=children)
    if phase == "en_cours":
        return _state(
            RUNNING,
            "sermon.processing",
            since=since,
            stuck=_older_than(since, PROCESSING_STUCK_AFTER, now),
            children=children,
        )
    if phase == "echec":
        return _state(FAILED, "sermon.processing_failed", since=since, detail=processing.get("erreur") or "", children=children)
    worst = children["clips"]
    if worst[FAILED]:
        return _state(FAILED, "sermon.clips_failed", children=children)
    if worst[ATTENTION]:
        return _state(ATTENTION, "sermon.clips_outdated", children=children)
    if worst[RUNNING]:
        return _state(RUNNING, "sermon.clips_rendering", children=children)
    return _state(OK, "sermon.ready", children=children)
