"""Event programme: Friday khutba, dars, conference.

The programme lives in a JSON file editable by hand (`data/events.json`):

{
  "events": [
    {"kind": "dars", "title": "Tafsir de la sourate Al-Kahf", "speaker": "Imam Abdallah",
     "series": "Tafsir Al-Kahf", "session": 7,
     "recurrence": {"weekday": "mardi", "after": "isha", "offset_min": 15}, "max_min": 90},
    {"kind": "conference", "title": "Les droits des voisins", "speaker": "Cheikh X",
     "date": "2026-11-21", "time": "18:30", "publish": false}
  ]
}

An event has either a `date` + `time` (one-off), or a weekly `recurrence`:
`weekday` (in French), and the time — absolute (`time`) or relative to a prayer (`after` = dhuhr, asr,
maghrib, isha + `offset_min`), recomputed each day with the Mawaqit times.

No I/O here apart from the programme file itself (tolerant read, atomic write): all the
computation is testable.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from .text import JOURS

KINDS = {"khutba", "dars", "conference"}
PRAYERS = ("dhuhr", "asr", "maghrib", "isha")
KIND_LABELS = {"khutba": "Khutba", "dars": "Dars", "conference": "Conférence"}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def parse_hhmm(value: str) -> dt.time:
    h, m = str(value).strip().split(":")[:2]
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"heure invalide : {value!r}")
    return dt.time(h, m)


def _as_datetime(day: dt.date, value) -> dt.datetime:
    """« 20:30 », time or datetime -> datetime of the requested day (datetime: time preserved)."""
    if isinstance(value, dt.datetime):
        return dt.datetime.combine(day, value.time())
    if isinstance(value, dt.time):
        return dt.datetime.combine(day, value)
    return dt.datetime.combine(day, parse_hhmm(value))


@dataclass
class EventInstance:
    starts: dt.datetime
    kind: str = "dars"
    title: str = ""
    speaker: str = ""
    series: str = ""
    session: Optional[int] = None
    max_min: Optional[float] = None
    publish: bool = True
    uid: str = ""

    def as_event(self) -> dict:
        """Canonical form of an event: the keys read by the machine and the scenes (those of SessionRec)."""
        return {"kind": self.kind, "title": self.title, "speaker": self.speaker,
                "series": self.series, "session_no": self.session}

    @property
    def label(self) -> str:
        return f"{kind_label(self.kind)} : {self.title}" if self.title else kind_label(self.kind)


@dataclass
class Recurrence:
    weekday: int
    time: Optional[str] = None
    after: Optional[str] = None
    offset_min: float = 0.0


@dataclass
class Event:
    raw: dict = field(default_factory=dict)
    kind: str = "dars"
    title: str = ""
    speaker: str = ""
    series: str = ""
    session: Optional[int] = None
    max_min: Optional[float] = None
    publish: bool = True
    date: Optional[dt.date] = None
    time: Optional[dt.time] = None
    recurrence: Optional[Recurrence] = None
    uid: str = ""

    def occurs_on(self, day: dt.date) -> bool:
        if self.date is not None:
            return self.date == day
        return self.recurrence is not None and self.recurrence.weekday == day.weekday()

    def starts_on(self, day: dt.date, times: Mapping[str, object] | None = None) -> Optional[dt.datetime]:
        """Start time on that day, or None if it cannot be computed (prayer missing)."""
        if not self.occurs_on(day):
            return None
        if self.date is not None and self.time is not None:
            return dt.datetime.combine(day, self.time)
        rec = self.recurrence
        if rec is None:
            return None
        if rec.time:
            return dt.datetime.combine(day, parse_hhmm(rec.time))
        if rec.after:
            base = (times or {}).get(rec.after)
            if base is None:
                return None
            return _as_datetime(day, base) + dt.timedelta(minutes=float(rec.offset_min or 0))
        return None

    def instance(self, day: dt.date, times: Mapping[str, object] | None = None) -> Optional[EventInstance]:
        start = self.starts_on(day, times)
        if start is None:
            return None
        return EventInstance(starts=start, kind=self.kind, title=self.title, speaker=self.speaker,
                             series=self.series, session=self.session, max_min=self.max_min,
                             publish=self.publish, uid=self.uid)


def uid_of(raw: Mapping, idx: int) -> str:
    """Stable identifier of a raw entry: its `uid`, or `kind-index`."""
    return str(raw.get("uid") or f"{str(raw.get('kind', 'dars')).strip().lower()}-{idx}")


def _parse(raw: dict, idx: int) -> Event:
    where = f"événement #{idx + 1}" + (f" ({raw.get('title')!r})" if raw.get("title") else "")
    if not isinstance(raw, dict):
        raise ValueError(f"{where} : objet JSON attendu")
    kind = str(raw.get("kind", "dars")).strip().lower()
    if kind not in KINDS:
        raise ValueError(f"{where} : type inconnu {kind!r} (attendu : {sorted(KINDS)})")
    title = str(raw.get("title", "")).strip()
    if not title:
        raise ValueError(f"{where} : titre manquant")
    rec = None
    if raw.get("recurrence"):
        r = raw["recurrence"]
        if not isinstance(r, dict):
            raise ValueError(f"{where} : « recurrence » doit être un objet")
        wd = str(r.get("weekday", "")).strip().lower()
        if wd not in JOURS:
            raise ValueError(f"{where} : jour de récurrence inconnu {wd!r} (attendu : {JOURS})")
        after = str(r.get("after", "")).strip().lower() or None
        if after and after not in PRAYERS:
            raise ValueError(f"{where} : prière inconnue {after!r} (attendu : {PRAYERS})")
        if not after and not r.get("time"):
            raise ValueError(f"{where} : une récurrence demande « time » ou « after »")
        rec = Recurrence(weekday=JOURS.index(wd), time=r.get("time"), after=after,
                         offset_min=float(r.get("offset_min", 0) or 0))
    date = time = None
    if raw.get("date"):
        try:
            date = dt.date.fromisoformat(str(raw["date"]))
        except ValueError as e:
            raise ValueError(f"{where} : date invalide {raw['date']!r}") from e
        if not raw.get("time"):
            raise ValueError(f"{where} : une date demande aussi une heure")
        time = parse_hhmm(raw["time"])
    if date is None and rec is None:
        raise ValueError(f"{where} : ni « date » ni « recurrence »")
    session = raw.get("session")
    return Event(raw=raw, kind=kind, title=title, speaker=str(raw.get("speaker", "") or "").strip(),
                 series=str(raw.get("series", "") or "").strip(),
                 session=int(session) if session not in (None, "") else None,
                 max_min=float(raw["max_min"]) if raw.get("max_min") else None,
                 publish=bool(raw.get("publish", True)), date=date, time=time, recurrence=rec,
                 uid=uid_of(raw, idx))


def loads(text: str) -> list[Event]:
    data = json.loads(text or "{}")
    items = data.get("events", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("« events » doit être une liste")
    return [_parse(raw, i) for i, raw in enumerate(items)]


def load(path: str | Path) -> tuple[list[Event], Optional[str]]:
    """(events, readable error). A missing file is not an error: the programme is empty."""
    p = Path(path)
    if not p.exists():
        return [], None
    try:
        return loads(p.read_text(encoding="utf-8")), None
    except (ValueError, json.JSONDecodeError) as e:
        return [], f"programme illisible ({p}) : {e}"


def parse(raw: dict, idx: int = 0) -> Event:
    """One entry, validated with the same rules as the ones read from the file."""
    return _parse(raw, idx)


def read_raw(path: str | Path) -> list[dict]:
    """Raw entries of the programme, validated and carrying a uid; unknown keys are kept.

    A missing file is an empty programme. An unreadable one raises ValueError: callers must
    never replace a file they could not read.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
        items = data.get("events", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("« events » doit être une liste")
        for i, raw in enumerate(items):
            _parse(raw, i)
    except (ValueError, OSError) as e:
        raise ValueError(str(e)) from e
    for i, raw in enumerate(items):
        raw.setdefault("uid", uid_of(raw, i))       # same value as the one deduced from the position
    return items


def write_raw(path: str | Path, items: Iterable[dict]) -> None:
    """Atomically replace the programme: a reader never sees a half-written file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"events": list(items)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def instances_on(events: Iterable[Event], day: dt.date,
                 times: Mapping[str, object] | None = None) -> list[EventInstance]:
    out = []
    for ev in events:
        if not ev.publish:
            continue
        inst = ev.instance(day, times)
        if inst is not None:
            out.append(inst)
    return sorted(out, key=lambda i: i.starts)


def next_instances(events: Iterable[Event], after: dt.datetime, count: int = 2,
                   days: int = 21,
                   times_for: Callable[[dt.date], Mapping[str, object] | None] | None = None) -> list[EventInstance]:
    """Next events after `after` (Mawaqit times supplied by `times_for`)."""
    events = [e for e in events if e.publish]
    found: list[EventInstance] = []
    for k in range(days + 1):
        day = after.date() + dt.timedelta(days=k)
        times = times_for(day) if times_for else None
        for inst in instances_on(events, day, times):
            if inst.starts > after:
                found.append(inst)
        if len(found) >= count:
            break
    return found[:count]
