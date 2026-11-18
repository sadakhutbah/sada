"""Tarawih of a mosque: how the Ramadan night prayers are broadcast.

No prayer is ever filmed by default. Tarawih is the one exception, and only as the mosque chose:

- ``off``: nothing is broadcast;
- ``audio`` (default): the recitation is heard, a fixed full screen replaces the camera;
- ``video``: camera and sound. Refused unless the request confirms it explicitly (the mosque takes
  responsibility for filming the prayer); who confirmed it and when is kept and audited.

Nothing is planned outside the period (start and end dates), so a forgotten setting cannot run all
year. Each night starts ``offset_min`` after isha, recomputed by the engine with the Mawaqit times:
isha itself is never broadcast.
"""

from __future__ import annotations

import datetime as dt

from ..events import TARAWIH_MODES
from ..i18n import _
from . import db, orm
from .models import Tarawih

DEFAULT_MODE = "audio"
DEFAULT_OFFSET_MIN, MIN_OFFSET_MIN, MAX_OFFSET_MIN = 20, 5, 120
DEFAULT_MAX_MIN, MIN_MAX_MIN, MAX_MAX_MIN = 90, 15, 240
MAX_PERIOD_DAYS = 31  # a Ramadan, with a day of margin for the moon sighting
MAX_IMAM_LEN = 80
UID = "tarawih"


class InvalidTarawih(ValueError):
    """Readable refusal of a setting."""


def _row(conn, mosque_id: int) -> dict:
    return orm.first(conn, Tarawih, mosque_id=mosque_id) or {}


def view(conn, mosque_id: int) -> dict:
    """What the Settings tab shows: saved values (defaults when nothing is saved) and the bounds."""
    r = _row(conn, mosque_id)
    return {
        "mode": r.get("mode") or DEFAULT_MODE,
        "start": r.get("start_date") or "",
        "end": r.get("end_date") or "",
        "offset_min": r.get("offset_min") or DEFAULT_OFFSET_MIN,
        "max_min": r.get("max_min") or DEFAULT_MAX_MIN,
        "imam": r.get("imam") or "",
        "video_confirmed_at": r.get("video_confirmed_at") if r.get("mode") == "video" else None,
        "modes": list(TARAWIH_MODES),
        "bounds": {
            "offset_min": [MIN_OFFSET_MIN, MAX_OFFSET_MIN],
            "max_min": [MIN_MAX_MIN, MAX_MAX_MIN],
            "period_days": MAX_PERIOD_DAYS,
        },
    }


def _int(value, lo: int, hi: int, default: int, message: str) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        return default
    try:
        n = int(float(text.replace(",", ".")))
    except ValueError as e:
        raise InvalidTarawih(message) from e
    if not lo <= n <= hi:
        raise InvalidTarawih(message)
    return n


def _date(value) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError as e:
        raise InvalidTarawih(_("Invalid date: {date}.", date=text)) from e


def _yes(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes", "oui")


def check(form: dict) -> dict:
    """Validated values of a form (text values, as the API receives them). Raises InvalidTarawih."""
    mode = str(form.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in TARAWIH_MODES:
        raise InvalidTarawih(_("Unknown tarawih mode: {mode}.", mode=mode))
    if mode == "video" and not _yes(form.get("confirm_video")):
        raise InvalidTarawih(
            _("Filming the prayer needs an explicit confirmation: tick the box, the mosque is responsible for it.")
        )
    start, end = _date(form.get("start")), _date(form.get("end"))
    if (start is None) != (end is None):
        raise InvalidTarawih(_("Give both the first and the last night of the period."))
    if start is not None and end is not None:
        if end < start:
            raise InvalidTarawih(_("The period ends before it starts."))
        if (end - start).days + 1 > MAX_PERIOD_DAYS:
            raise InvalidTarawih(_("The period cannot exceed {days} nights.", days=MAX_PERIOD_DAYS))
    imam = " ".join(str(form.get("imam") or "").split())
    if len(imam) > MAX_IMAM_LEN:
        raise InvalidTarawih(_("Imam name too long ({max} characters at most).", max=MAX_IMAM_LEN))
    return {
        "mode": mode,
        "start_date": start.isoformat() if start else "",
        "end_date": end.isoformat() if end else "",
        "offset_min": _int(
            form.get("offset_min"),
            MIN_OFFSET_MIN,
            MAX_OFFSET_MIN,
            DEFAULT_OFFSET_MIN,
            _("Delay after isha: from {min} to {max} minutes.", min=MIN_OFFSET_MIN, max=MAX_OFFSET_MIN),
        ),
        "max_min": _int(
            form.get("max_min"),
            MIN_MAX_MIN,
            MAX_MAX_MIN,
            DEFAULT_MAX_MIN,
            _("Maximum duration: from {min} to {max} minutes.", min=MIN_MAX_MIN, max=MAX_MAX_MIN),
        ),
        "imam": imam,
    }


def save(conn, mosque: dict, form: dict, user: dict | None, ip: str | None = None) -> dict:
    vals = check(form)
    before = _row(conn, mosque["id"])
    now = db.now_iso()
    video = vals["mode"] == "video"
    keep = video and before.get("mode") == "video"  # still filmed: the first confirmation stands
    orm.upsert(
        conn,
        Tarawih,
        ("mosque_id",),
        mosque_id=mosque["id"],
        **vals,
        video_confirmed_by=(before.get("video_confirmed_by") if keep else (user or {}).get("id")) if video else None,
        video_confirmed_at=(before.get("video_confirmed_at") if keep else now) if video else None,
        updated_at=now,
    )
    db.audit(
        conn,
        mosque["id"],
        (user or {}).get("id"),
        "tarawih.configure",
        "mosque",
        mosque["id"],
        ip=ip,
        mode=vals["mode"],
        mode_avant=before.get("mode") or "",
        debut=vals["start_date"],
        fin=vals["end_date"],
        video_confirmee=video,
    )
    return view(conn, mosque["id"])


def event(conn, mosque_id: int) -> dict | None:
    """The nightly tarawih as a raw programme entry for the engine (`sada.events`), or None when
    nothing is to be broadcast (mode off, no period)."""
    r = _row(conn, mosque_id)
    mode = r.get("mode") or DEFAULT_MODE
    if mode == "off" or not r.get("start_date") or not r.get("end_date"):
        return None
    if mode == "video" and not r.get("video_confirmed_at"):
        mode = "audio"  # never filmed without a recorded confirmation
    return {
        "uid": UID,
        "kind": "tarawih",
        "title": "Tarawih",
        "speaker": r.get("imam") or "",
        "mode": mode,
        "period": {"from": r["start_date"], "until": r["end_date"]},
        "recurrence": {"after": "isha", "offset_min": r.get("offset_min") or DEFAULT_OFFSET_MIN},
        "max_min": r.get("max_min") or DEFAULT_MAX_MIN,
    }
