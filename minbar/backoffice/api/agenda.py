"""Schedule of dars and conferences (`events.json`), through `minbar.events`."""
from __future__ import annotations

import datetime as dt
import re
import secrets
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from ... import events as events_mod
from ...text import JOURS
from .. import db
from ..context import Ctx, CtxDep, Invalid, NotFound
from .common import _ok

router = APIRouter()


# ------------------------------------------------------------------ programme: dars and conferences

AGENDA_KINDS = ("dars", "conference")       # the Friday khutba is not an event to enter
_agenda_lock = threading.Lock()


def _agenda_path(request: Request) -> Path:
    """Programme file, handed to the application by `create_app` (`events_file`)."""
    return Path(request.app.state.events_file)


def _agenda_read(path: Path) -> list[dict]:
    """Raw events of the file. An unreadable file is refused: we never overwrite it."""
    try:
        return events_mod.read_raw(path)
    except ValueError as e:
        raise Invalid(f"Le programme est illisible, corrigez-le d'abord : {e}") from e


def _agenda_clean(f: dict, uid: str) -> dict:
    """Form (flat fields, as text) -> canonical event, validated by the engine itself."""
    s = lambda k: (f.get(k) or "").strip()                                   # noqa: E731
    kind = s("kind").lower() or "dars"
    if kind not in AGENDA_KINDS:
        raise Invalid("Type inconnu : choisissez un dars ou une conférence.")
    raw: dict = {"uid": uid, "kind": kind, "title": s("title")[:200], "speaker": s("speaker")[:120],
                 "series": s("series")[:200], "publish": s("publish").lower() not in ("0", "false", "non", "off")}
    if s("session"):
        try:
            raw["session"] = int(s("session"))
        except ValueError as e:
            raise Invalid("Le numéro de séance doit être un nombre entier.") from e
        if raw["session"] < 1:
            raise Invalid("Le numéro de séance doit être positif.")
    if s("max_min"):
        try:
            raw["max_min"] = float(s("max_min").replace(",", "."))
        except ValueError as e:
            raise Invalid("La durée maximale doit être un nombre de minutes.") from e
        if raw["max_min"] <= 0:
            raise Invalid("La durée maximale doit être positive.")
    if s("mode") == "weekly":
        rec: dict = {"weekday": s("weekday").lower()}
        if s("after"):
            rec["after"] = s("after").lower()
            try:
                rec["offset_min"] = float(s("offset_min").replace(",", ".") or 0)
            except ValueError as e:
                raise Invalid("Le décalage doit être un nombre de minutes.") from e
        else:
            rec["time"] = s("time")
        raw["recurrence"] = rec
    else:
        raw["date"], raw["time"] = s("date"), s("time")
    try:
        events_mod.parse(raw)
    except ValueError as e:
        raise Invalid(re.sub(r"^événement #\d+( \([^)]*\))? : ", "", str(e)).capitalize() + ".") from e
    return raw


def _agenda_item(raw: dict, idx: int, today: dt.date) -> dict:
    """A row of the list: the event and its next occurrence (date only: the time relative to
    a prayer depends on the day's timetable)."""
    ev = events_mod.parse(raw)
    rec = raw.get("recurrence") or None
    nxt = None
    if ev.date is not None:
        nxt = ev.date
    elif ev.recurrence is not None:
        nxt = today + dt.timedelta(days=(ev.recurrence.weekday - today.weekday()) % 7)
    return {"id": events_mod.uid_of(raw, idx), "kind": ev.kind, "kind_label": events_mod.kind_label(ev.kind),
            "title": ev.title, "speaker": ev.speaker, "series": ev.series, "session": ev.session,
            "max_min": ev.max_min, "publish": ev.publish,
            "mode": "weekly" if rec else "once",
            "date": raw.get("date") or "", "time": raw.get("time") or (rec or {}).get("time") or "",
            "weekday": (rec or {}).get("weekday") or "", "after": (rec or {}).get("after") or "",
            "offset_min": (rec or {}).get("offset_min") or 0,
            "next": nxt.isoformat() if nxt else "", "past": bool(ev.date and ev.date < today)}


def _agenda_today(ctx: Ctx) -> dt.date:
    try:
        return dt.datetime.now(db.timezone(ctx.mosque)).date()
    except Exception:                                  # noqa: BLE001 — unknown timezone: machine time
        return dt.date.today()


def _agenda_list(ctx: Ctx) -> dict:
    items = _agenda_read(_agenda_path(ctx.request))
    today = _agenda_today(ctx)
    rows = [_agenda_item(r, i, today) for i, r in enumerate(items) if str(r.get("kind", "dars")).lower() in AGENDA_KINDS]
    rows.sort(key=lambda r: (r["past"], r["next"] or "9999", r["time"] or "99:99", r["title"].lower()))
    return {"items": rows, "total": len(rows), "kinds": {k: events_mod.kind_label(k) for k in AGENDA_KINDS},
            "weekdays": list(JOURS), "prayers": list(events_mod.PRAYERS), "today": today.isoformat()}


@router.get("/events")
def events_list(ctx: CtxDep):
    """Schedule of dars and conferences, next occurrences first, past events at the end."""
    return _agenda_list(ctx)


@router.post("/events")
async def event_create(ctx: CtxDep):
    f = await ctx.body()
    ctx.require("assign")
    path = _agenda_path(ctx.request)
    with _agenda_lock:
        items = _agenda_read(path)
        raw = _agenda_clean(f, secrets.token_hex(4))
        events_mod.write_raw(path, [*items, raw])
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "programme.cree", "programme", None, ip=ctx.ip,
             uid=raw["uid"], type=raw["kind"], titre=raw["title"])
    return _ok(f"« {raw['title']} » ajouté au programme.", id=raw["uid"])


@router.put("/events/{uid}")
async def event_update(uid: str, ctx: CtxDep):
    f = await ctx.body()
    ctx.require("assign")
    path = _agenda_path(ctx.request)
    with _agenda_lock:
        items = _agenda_read(path)
        pos = next((i for i, r in enumerate(items) if events_mod.uid_of(r, i) == uid), None)
        if pos is None:
            raise NotFound()
        raw = _agenda_clean(f, uid)
        items[pos] = raw           # the identifier (uid) is written: it no longer depends on the position
        events_mod.write_raw(path, items)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "programme.modifie", "programme", None, ip=ctx.ip,
             uid=uid, type=raw["kind"], titre=raw["title"])
    return _ok(f"« {raw['title']} » mis à jour.", id=uid)


@router.delete("/events/{uid}")
async def event_delete(uid: str, ctx: CtxDep):
    await ctx.body()                  # checks the anti-replay token
    ctx.require("assign")
    path = _agenda_path(ctx.request)
    with _agenda_lock:
        items = _agenda_read(path)
        pos = next((i for i, r in enumerate(items) if events_mod.uid_of(r, i) == uid), None)
        if pos is None:
            raise NotFound()
        gone = items.pop(pos)
        events_mod.write_raw(path, items)
    db.audit(ctx.conn, ctx.mosque["id"], ctx.user["id"], "programme.supprime", "programme", None, ip=ctx.ip,
             uid=uid, type=gone.get("kind"), titre=gone.get("title"))
    return _ok(f"« {gone.get('title')} » retiré du programme.")
