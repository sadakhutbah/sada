"""Prayer times of a mosque: the one place that knows where they come from.

Three sources, chosen per mosque:

- ``mawaqit``: read from the mosque's Mawaqit page (kept in the internal cache, `mawaqit_store`, which
  the live needs). Sada neither stores a calendar of it nor publishes it: a Mawaqit mosque has nothing
  to enter, and its public page stays Mawaqit's.
- ``computed``: calculated from the mosque's coordinates (`sada.prayer_calc`), with an iqama delay per
  prayer and, where the mosque wants, a typed correction for a given day.
- ``manual``: the mosque types its own timetable (a month grid, or a CSV), day by day.

Whatever the source, consumers (the orchestrator that arms the live, the programme, the tarawih, the
screens, the public endpoint) call `times()` and read the shape described in `sada.prayers`.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time
from urllib.parse import urlparse

from sqlalchemy import delete, insert, select

from .. import prayer_calc as calc
from .. import prayers
from ..i18n import _
from . import db, mawaqit_store, orm
from .models import PrayerDay, PrayerSettings

SOURCES = ("mawaqit", "computed", "manual")
PRAYERS = prayers.NAMES  # fajr, dhuhr, asr, maghrib, isha
FIELDS = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")
IQAMA_FIELDS = tuple(f"{p}_iqama" for p in PRAYERS)
MAX_JUMUA = 3
MAX_IQAMA_DELAY_MIN = 120
MAX_IMPORT_ROWS = 400  # a year plus margin
MAX_ERRORS_SHOWN = 8
MAWAQIT_HOSTS = ("mawaqit.net",)

_TIME = re.compile(r"^(\d{1,2})[:h.](\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class NoTimes(ValueError):
    """No time can be given for this mosque (nothing chosen, or nothing entered)."""


# ------------------------------------------------------------------ values


def norm_time(value) -> str:
    """"7:05", "07h05", "07.05" -> "07:05"; '' stays ''; anything else is refused."""
    v = str(value or "").strip()
    if not v:
        return ""
    m = _TIME.match(v)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(_("{value} is not a time (HH:MM).", value=v))
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _after(prayer: str, hhmm: str) -> int:
    """Minutes of the day, Isha after midnight counted as late (so 00:20 follows 23:50)."""
    v = _minutes(hhmm)
    return v + 1440 if prayer == "isha" and v < 360 else v


def clean_day(raw: dict) -> dict:
    """The row to store for one day: times normalised, an iqama "+10" resolved against its adhan.

    Raises ValueError (readable) on a bad time, times out of order, or an iqama before its adhan.
    Returns {} when the day holds nothing.
    """
    out = {k: norm_time(raw.get(k)) for k in FIELDS}
    order = [p for p in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha") if out[p]]
    for a, b in zip(order, order[1:], strict=False):
        if _after(a, out[a]) >= _after(b, out[b]):
            raise ValueError(_("{later} must come after {earlier}.", later=b, earlier=a))
    for p in PRAYERS:
        v = str(raw.get(f"{p}_iqama") or "").strip()
        if v.startswith("+"):
            if not out[p]:
                raise ValueError(_("An iqama delay needs the {prayer} time.", prayer=p))
            try:
                delay = int(v[1:])
            except ValueError as e:
                raise ValueError(_("{value} is not a time (HH:MM).", value=v)) from e
            if not 0 <= delay <= MAX_IQAMA_DELAY_MIN:
                raise ValueError(_("An iqama delay is between 0 and {n} minutes.", n=MAX_IQAMA_DELAY_MIN))
            total = (_minutes(out[p]) + delay) % 1440
            v = f"{total // 60:02d}:{total % 60:02d}"
        else:
            v = norm_time(v)
        if v and out[p] and _after(p, v) < _after(p, out[p]):
            raise ValueError(_("The {prayer} iqama cannot be before its adhan.", prayer=p))
        out[f"{p}_iqama"] = v
    return out if any(out.values()) else {}


def _day_key(day: dt.date | str) -> str:
    return day if isinstance(day, str) else day.isoformat()


def normalize_mawaqit(value: str) -> str:
    """The Mawaqit page of the mosque from a pasted address or just its identifier.

    Only mawaqit.net is accepted: the address is fetched by the server, so any other host is refused.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if "://" not in v and "/" not in v:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", v):
            raise ValueError(_("This is neither a Mawaqit address nor a Mawaqit identifier."))
        return f"https://mawaqit.net/fr/{v}"
    u = urlparse(v if "://" in v else "https://" + v)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in MAWAQIT_HOSTS):
        raise ValueError(_("This is neither a Mawaqit address nor a Mawaqit identifier."))
    return f"https://{host}{u.path}".rstrip("/")


# ------------------------------------------------------------------ settings


def read(conn, mosque: dict) -> dict:
    """The mosque's source and options. No row yet: Mawaqit when an address exists, else '' (unchosen)."""
    row = orm.first(conn, PrayerSettings, mosque_id=mosque["id"])
    try:
        cfg = json.loads((row or {}).get("calc") or "{}")
    except ValueError:
        cfg = {}
    try:
        jumua = [t for t in json.loads((row or {}).get("jumua") or "[]") if isinstance(t, str)]
    except ValueError:
        jumua = []
    source = row["source"] if row else ("mawaqit" if mosque.get("mawaqit_url") else "")
    return {
        "source": source,
        "public": bool(row["public"]) if row else True,
        "jumua": jumua,
        "calc": cfg if isinstance(cfg, dict) else {},
    }


def save(conn, mosque: dict, *, source: str, public: bool, jumua: list, cfg: dict | None) -> dict:
    """Validate and store the source choice. A computed source needs a calculation that works."""
    if source not in SOURCES:
        raise ValueError(_("Unknown source of prayer times."))
    times = []
    for t in jumua:
        t = norm_time(t)
        if t:
            times.append(t)
    if len(times) > MAX_JUMUA:
        raise ValueError(_("At most {n} jumu'a times.", n=MAX_JUMUA))
    cfg = dict(cfg or {})
    if source == "computed":
        calc.params_from(cfg, _tz_key(mosque))
        cfg["iqama"] = _iqama_delays(cfg.get("iqama"))
    orm.upsert(
        conn,
        PrayerSettings,
        ("mosque_id",),
        mosque_id=mosque["id"],
        source=source,
        public=1 if public and source != "mawaqit" else 0,
        jumua=json.dumps(sorted(set(times))),
        calc=json.dumps(cfg, ensure_ascii=False),
        updated_at=db.now_iso(),
    )
    return read(conn, mosque)


def _iqama_delays(raw) -> dict:
    out = {}
    for p, v in (raw or {}).items():
        if p not in PRAYERS or v in (None, "", 0, "0"):
            continue
        try:
            n = int(float(str(v)))
        except ValueError as e:
            raise ValueError(_("An iqama delay is between 0 and {n} minutes.", n=MAX_IQAMA_DELAY_MIN)) from e
        if not 0 < n <= MAX_IQAMA_DELAY_MIN:
            raise ValueError(_("An iqama delay is between 0 and {n} minutes.", n=MAX_IQAMA_DELAY_MIN))
        out[p] = n
    return out


def _tz_key(mosque: dict) -> str:
    return mosque.get("timezone") or db.DEFAULT_TZ


def _params(cfg: dict, mosque: dict) -> calc.Params:
    return calc.params_from(cfg, _tz_key(mosque))


# ------------------------------------------------------------------ days


def stored(conn, mosque_id: int, start: str, end: str) -> dict[str, dict]:
    """Typed rows of the mosque between two ISO dates (inclusive)."""
    stmt = (
        select(PrayerDay)
        .where(PrayerDay.mosque_id == mosque_id, PrayerDay.day >= start, PrayerDay.day <= end)
        .order_by(PrayerDay.day)
    )
    return {r["day"]: r for r in orm.rows(conn, stmt)}


def save_days(conn, mosque_id: int, days: dict[str, dict], *, replace_range: tuple[str, str] | None = None) -> int:
    """Store rows (already cleaned). Empty rows delete the day. With `replace_range`, every day of that
    range is replaced by `days` (one transaction: never half written). Returns the days stored."""
    rows = [{"mosque_id": mosque_id, "day": d, **{k: v.get(k, "") for k in (*FIELDS, *IQAMA_FIELDS)}} for d, v in days.items() if v]
    with orm.transaction(conn):
        if replace_range:
            conn.execute(
                delete(PrayerDay).where(
                    PrayerDay.mosque_id == mosque_id, PrayerDay.day >= replace_range[0], PrayerDay.day <= replace_range[1]
                )
            )
        else:
            empty = [d for d, v in days.items() if not v]
            doomed = [r["day"] for r in rows] + empty
            if doomed:
                conn.execute(delete(PrayerDay).where(PrayerDay.mosque_id == mosque_id, PrayerDay.day.in_(doomed)))
        if rows:
            conn.execute(insert(PrayerDay), rows)
    return len(rows)


def month_bounds(month: str) -> tuple[str, str]:
    try:
        first = dt.date.fromisoformat(month + "-01")
    except ValueError as e:
        raise ValueError(_("A month looks like 2027-03.")) from e
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return first.isoformat(), last.isoformat()


def computed_day(cfg: dict, mosque: dict, day: dt.date) -> dict:
    """Calculated times of a day with the iqama delays of the settings: the fields of a stored row."""
    t = calc.day_times(day, _params(cfg, mosque))
    out = dict(t)
    for p, n in (cfg.get("iqama") or {}).items():
        if t.get(p):
            total = (_minutes(t[p]) + int(n)) % 1440
            out[f"{p}_iqama"] = f"{total // 60:02d}:{total % 60:02d}"
    return out


def day_fields(conn, mosque: dict, st: dict, day: dt.date, typed: dict | None = None) -> dict:
    """What the mosque shows for one day under its source: the calculation, corrected by what it typed
    (computed), or only what it typed (manual). Never anything for a Mawaqit mosque."""
    if typed is None:
        typed = stored(conn, mosque["id"], _day_key(day), _day_key(day)).get(_day_key(day)) or {}
    typed = {k: v for k, v in typed.items() if k in (*FIELDS, *IQAMA_FIELDS) and v}
    if st["source"] == "computed":
        return {**computed_day(st["calc"], mosque, day), **typed}
    return typed if st["source"] == "manual" else {}


def to_data(fields: dict, st: dict, mosque: dict, origin: str) -> dict:
    """A day's fields as the shape every consumer reads (see `sada.prayers`)."""
    times = [fields.get(p, "") for p in PRAYERS]
    iqama = [fields.get(f"{p}_iqama", "") for p in PRAYERS]
    return prayers.finish(
        {
            "name": mosque.get("name"),
            "times": times if any(times) else [],
            "sunrise": fields.get("sunrise", ""),
            "iqama": iqama if any(iqama) else None,
            "jumua_list": list(st["jumua"]),
            "origin": origin,
            "source": st["source"],
        }
    )


def local_today(mosque: dict, now: float | None = None) -> dt.date:
    when = dt.datetime.fromtimestamp(time.time() if now is None else now, dt.UTC)
    return when.astimezone(db.timezone(mosque)).date()


def times(conn, mosque: dict, fetch=None, now: float | None = None) -> dict:
    """Today's times of the mosque, from its source. THE entry point for everything that needs them.

    Raises NoTimes when the mosque has no usable source (the orchestrator then falls back locally).
    """
    st = read(conn, mosque)
    if st["source"] in ("computed", "manual"):
        day = local_today(mosque, now)
        try:
            fields = day_fields(conn, mosque, st, day)
        except calc.InvalidParams as e:
            raise NoTimes(str(e)) from e
        data = to_data(fields, st, mosque, st["source"])
        if not data["times"] and not data["jumua_list"]:
            raise NoTimes(_("No prayer time entered for today."))
        return data
    if not st["source"]:
        raise NoTimes(_("No source of prayer times chosen."))
    data = mawaqit_store.times(conn, mosque, fetch=fetch, now=now)
    data["source"] = "mawaqit"
    return data


# ------------------------------------------------------------------ CSV

_ALIASES = {
    "date": "date", "jour": "date", "day": "date",
    "fajr": "fajr", "sobh": "fajr", "subh": "fajr", "sunrise": "sunrise", "shuruq": "sunrise", "chourouk": "sunrise",
    "lever": "sunrise", "dhuhr": "dhuhr", "zuhr": "dhuhr", "dohr": "dhuhr", "duhr": "dhuhr", "asr": "asr",
    "maghrib": "maghrib", "isha": "isha", "icha": "isha",
}  # fmt: skip


def _header(cells: list[str]) -> list[str | None]:
    out = []
    for c in cells:
        k = re.sub(r"[^a-z_]", "", c.strip().lower().replace("-", "_").replace(" ", "_"))
        base, _sep, suffix = k.partition("_")
        if suffix in ("iqama", "iqamah") and _ALIASES.get(base) in PRAYERS:
            out.append(f"{_ALIASES[base]}_iqama")
        else:
            out.append(_ALIASES.get(k))
    return out


def _date(cell: str) -> str:
    c = cell.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(c, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(_("{value} is not a date (YYYY-MM-DD or DD/MM/YYYY).", value=c))


def parse_csv(text: str) -> tuple[dict[str, dict], list[dict]]:
    """({day: cleaned row}, [{line, message}]). A header row names the columns; without one the order is
    date, fajr, dhuhr, asr, maghrib, isha. Separator: comma, semicolon or tab."""
    text = (text or "").lstrip("\ufeff")
    sample = text[:2000]
    delim = max(",;\t", key=sample.count) if any(d in sample for d in ",;\t") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    days: dict[str, dict] = {}
    errors: list[dict] = []
    if not rows:
        return days, [{"line": 0, "message": _("The file is empty.")}]
    if len(rows) > MAX_IMPORT_ROWS + 1:
        return days, [{"line": 0, "message": _("At most {n} days at a time.", n=MAX_IMPORT_ROWS)}]
    head = _header(rows[0])
    start = 1
    if "date" not in head:
        head, start = ["date", "fajr", "dhuhr", "asr", "maghrib", "isha"], 0
    for i, r in enumerate(rows[start:], start=start + 1):
        try:
            raw = {h: c for h, c in zip(head, r, strict=False) if h}
            day = _date(raw.pop("date", ""))
            days[day] = clean_day(raw)
        except ValueError as e:
            errors.append({"line": i, "message": str(e)})
    return days, errors


def export_csv(conn, mosque: dict, year: int) -> str:
    """The year's effective times as a CSV the import reads back (a computed year turns into a manual one)."""
    st = read(conn, mosque)
    typed = stored(conn, mosque["id"], f"{year}-01-01", f"{year}-12-31")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["date", *FIELDS, *IQAMA_FIELDS])
    day = dt.date(year, 1, 1)
    while day.year == year:
        f = day_fields(conn, mosque, st, day, typed.get(day.isoformat()) or {})
        if st["source"] == "manual" and not f:
            day += dt.timedelta(days=1)
            continue
        w.writerow([day.isoformat(), *(f.get(k, "") for k in (*FIELDS, *IQAMA_FIELDS))])
        day += dt.timedelta(days=1)
    return out.getvalue()


# ------------------------------------------------------------------ month view, state, public payload


def month_view(conn, mosque: dict, month: str) -> list[dict]:
    """Every day of the month: what is typed, and what the calculation says (computed source)."""
    first, last = month_bounds(month)
    st = read(conn, mosque)
    typed = stored(conn, mosque["id"], first, last)
    out = []
    day = dt.date.fromisoformat(first)
    while day.isoformat() <= last:
        k = day.isoformat()
        row = {f: (typed.get(k) or {}).get(f, "") for f in (*FIELDS, *IQAMA_FIELDS)}
        entry = {"date": k, "set": row}
        if st["source"] == "computed":
            entry["calc"] = computed_day(st["calc"], mosque, day)
        out.append(entry)
        day += dt.timedelta(days=1)
    return out


def state(conn, mosque: dict, now: float | None = None) -> dict:
    """Entity state of the mosque's prayer times (database only: no network at render time)."""
    from .states import ATTENTION, OK, _state

    st = read(conn, mosque)
    src = st["source"]
    if not src:
        return _state(ATTENTION, "prayer.unset")
    if src == "mawaqit":
        return _state(OK if mosque.get("mawaqit_url") else ATTENTION, "prayer.mawaqit" if mosque.get("mawaqit_url") else "prayer.mawaqit_no_url")
    today = local_today(mosque, now)
    if src == "computed":
        try:
            _params(st["calc"], mosque)
        except calc.InvalidParams:
            return _state(ATTENTION, "prayer.computed_invalid")
        return _state(OK, "prayer.computed")
    ahead = stored(conn, mosque["id"], today.isoformat(), (today + dt.timedelta(days=60)).isoformat())
    if today.isoformat() not in ahead:
        return _state(ATTENTION, "prayer.manual_today_missing")
    days_left = len(ahead)
    last = max(ahead)
    covered = (dt.date.fromisoformat(last) - today).days + 1
    if covered < 14 and days_left < 14:
        return _state(ATTENTION, "prayer.manual_running_out", params={"days": covered})
    return _state(OK, "prayer.manual", params={"days": covered})


def view(conn, mosque: dict, now: float | None = None) -> dict:
    st = read(conn, mosque)
    return {
        "source": st["source"],
        "sources": list(SOURCES),
        "public": st["public"] and st["source"] in ("computed", "manual"),
        "jumua": st["jumua"],
        "mawaqit_url": mosque.get("mawaqit_url") or "",
        "calc": {**DEFAULT_CALC, **st["calc"]},
        "methods": {k: {"fajr": v[0], "isha": v[1], "isha_delay": v[2]} for k, v in calc.METHODS.items()},
        "asr": list(calc.ASR),
        "high_lats": list(calc.HIGH_LATS),
        "slug": mosque["slug"],
        "state": state(conn, mosque, now),
    }


DEFAULT_CALC = {
    "lat": "",
    "lon": "",
    "method": "uoif",
    "fajr_angle": "",
    "isha_angle": "",
    "isha_delay_min": "",
    "asr": "standard",
    "high_lats": "nightmiddle",
    "offsets": {},
    "bounds": {},
    "iqama": {},
}


def preview(mosque: dict, cfg: dict, month: str) -> list[dict]:
    """Calculated month for settings that are not saved yet: what the mosque sees before it chooses."""
    p = _params(cfg, mosque)
    delays = _iqama_delays(cfg.get("iqama"))
    first, last = month_bounds(month)
    out, day = [], dt.date.fromisoformat(first)
    while day.isoformat() <= last:
        t = calc.day_times(day, p)
        for prayer, n in delays.items():
            if t.get(prayer):
                total = (_minutes(t[prayer]) + n) % 1440
                t[f"{prayer}_iqama"] = f"{total // 60:02d}:{total % 60:02d}"
        out.append({"date": day.isoformat(), **t})
        day += dt.timedelta(days=1)
    return out


def public_payload(conn, mosque: dict, *, day: dt.date | None = None, month: str | None = None) -> dict:
    """What the public endpoint says of a mosque that publishes (computed or manual): never a secret,
    never an address, only the times. Raises NoTimes when a day has nothing."""
    st = read(conn, mosque)
    head = {
        "mosque": {"slug": mosque["slug"], "name": mosque["name"], "timezone": _tz_key(mosque)},
        "source": st["source"],
        "jumua": st["jumua"],
    }
    if month:
        first, last = month_bounds(month)
        typed = stored(conn, mosque["id"], first, last)
        days = []
        d = dt.date.fromisoformat(first)
        while d.isoformat() <= last:
            f = day_fields(conn, mosque, st, d, typed.get(d.isoformat()) or {})
            if f:
                days.append({"date": d.isoformat(), **_public_day(f)})
            d += dt.timedelta(days=1)
        return {**head, "month": month, "days": days}
    day = day or local_today(mosque)
    f = day_fields(conn, mosque, st, day)
    return {**head, "date": day.isoformat(), **(_public_day(f) if f else {"times": {}, "iqama": {}})}


def _public_day(f: dict) -> dict:
    times_ = {k: f[k] for k in FIELDS if f.get(k)}
    iqama = {p: f[f"{p}_iqama"] for p in PRAYERS if f.get(f"{p}_iqama")}
    return {"times": times_, "iqama": iqama}
