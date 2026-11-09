"""Programme des événements : khutba du vendredi, dars, conférence.

Le programme vit dans un fichier JSON éditable à la main (`data/events.json`) :

{
  "events": [
    {"kind": "dars", "title": "Tafsir de la sourate Al-Kahf", "speaker": "Imam Abdallah",
     "series": "Tafsir Al-Kahf", "session": 7,
     "recurrence": {"weekday": "mardi", "after": "isha", "offset_min": 15}, "max_min": 90},
    {"kind": "conference", "title": "Les droits des voisins", "speaker": "Cheikh X",
     "date": "2026-11-21", "time": "18:30", "publish": false}
  ]
}

Un événement a soit une `date` + `time` (une fois), soit une `recurrence` hebdomadaire :
`weekday` (en français), et l'heure — absolue (`time`) ou relative à une prière (`after` = dhuhr, asr,
maghrib, isha + `offset_min`), recalculée chaque jour avec les horaires Mawaqit.

Aucune E/S ici hormis la lecture du fichier : tout le calcul est testable.
"""
from __future__ import annotations

import datetime as dt
import json
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
    """« 20:30 », heure ou datetime -> datetime du jour demandé (datetime : heure conservée)."""
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
        """Forme canonique d'un événement : les clés que lisent la machine et les scènes (celles de SessionRec)."""
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
        """Heure de début ce jour-là, ou None si elle ne peut pas être calculée (prière absente)."""
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
                 uid=str(raw.get("uid") or f"{kind}-{idx}"))


def loads(text: str) -> list[Event]:
    data = json.loads(text or "{}")
    items = data.get("events", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("« events » doit être une liste")
    return [_parse(raw, i) for i, raw in enumerate(items)]


def load(path: str | Path) -> tuple[list[Event], Optional[str]]:
    """(événements, erreur lisible). Un fichier absent n'est pas une erreur : le programme est vide."""
    p = Path(path)
    if not p.exists():
        return [], None
    try:
        return loads(p.read_text(encoding="utf-8")), None
    except (ValueError, json.JSONDecodeError) as e:
        return [], f"programme illisible ({p}) : {e}"


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
    """Prochains événements après `after` (horaires Mawaqit fournis par `times_for`)."""
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
