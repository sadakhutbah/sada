"""Calculated prayer times: the astronomy comes from `praytimes` (the PrayTimes.org algorithm, LGPL-3.0,
compatible with this project's AGPL-3.0); this module only maps Sada's settings onto it.

A mosque picks a method (the Fajr/Isha angles, or a fixed delay for Isha), the Asr school, a rule for
high latitudes, its coordinates, and a per-prayer offset in minutes. Nothing here touches the network
or the database: the same settings and the same day always give the same times.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
import warnings
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from praytimes import PrayTimes

# `praytimes` passes `maxsplit` positionally (a DeprecationWarning on every call since Python 3.13)
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"praytimes\.praytimes")

#: Prayers of the day in order, `sunrise` included (shown, never armed on).
OUTPUT = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")

#: method -> (Fajr angle, Isha angle or None, Isha delay after maghrib in minutes or None)
METHODS: dict[str, tuple[float, float | None, float | None]] = {
    "uoif": (12.0, 12.0, None),  # Union des organisations islamiques de France
    "mwl": (18.0, 17.0, None),  # Muslim World League
    "isna": (15.0, 15.0, None),  # Islamic Society of North America
    "egypt": (19.5, 17.5, None),  # Egyptian General Authority of Survey
    "makkah": (18.5, None, 90.0),  # Umm al-Qura (Isha 90 min after maghrib)
    "karachi": (18.0, 18.0, None),  # University of Islamic Sciences, Karachi
    "morocco": (19.0, 17.0, None),  # Ministry of Habous, Morocco
    "diyanet": (18.0, 17.0, None),  # Presidency of Religious Affairs, Turkey
    "custom": (18.0, 17.0, None),  # angles chosen by the mosque (defaults are the MWL ones)
}
ASR = {"standard": "Standard", "hanafi": "Hanafi"}  # shafi'i / maliki / hanbali, then hanafi
HIGH_LATS = {"none": "None", "nightmiddle": "NightMiddle", "oneseventh": "OneSeventh", "anglebased": "AngleBased"}
MAX_OFFSET_MIN = 60
ANGLE_RANGE = (5.0, 25.0)
DELAY_RANGE = (30.0, 150.0)

_LOCK = threading.Lock()  # `praytimes` keeps its settings in class attributes: one calculation at a time


class InvalidParams(ValueError):
    """Readable refusal of a calculation setting."""


@dataclass(frozen=True)
class Params:
    lat: float
    lon: float
    timezone: str = "Europe/Paris"
    method: str = "uoif"
    fajr_angle: float | None = None  # only read when method == "custom"
    isha_angle: float | None = None
    isha_delay_min: float | None = None  # custom: a fixed delay after maghrib replaces the Isha angle
    asr: str = "standard"
    high_lats: str = "nightmiddle"
    offsets: dict[str, int] = field(default_factory=dict)  # minutes added to each of OUTPUT
    # Floor and ceiling of a prayer ("HH:MM", either may be ''): a mosque often never prays Isha before
    # 19:10 or Dhuhr before 13:00 whatever the sun says. Applied after the offset.
    bounds: dict[str, tuple[str, str]] = field(default_factory=dict)

    def angles(self) -> tuple[float, float | None, float | None]:
        """(Fajr angle, Isha angle, Isha delay) once the method is applied."""
        if self.method != "custom":
            return METHODS[self.method]
        fajr = METHODS["custom"][0] if self.fajr_angle is None else self.fajr_angle
        if self.isha_delay_min:
            return fajr, None, self.isha_delay_min
        return fajr, METHODS["custom"][1] if self.isha_angle is None else self.isha_angle, None


def check(p: Params) -> Params:
    """The parameters themselves, or InvalidParams (the message is meant for the person typing)."""
    from .i18n import _

    if not (-90 <= p.lat <= 90) or not (-180 <= p.lon <= 180):
        raise InvalidParams(_("Coordinates out of range: latitude between -90 and 90, longitude between -180 and 180."))
    if p.method not in METHODS:
        raise InvalidParams(_("Unknown calculation method."))
    if p.asr not in ASR:
        raise InvalidParams(_("Unknown Asr school."))
    if p.high_lats not in HIGH_LATS:
        raise InvalidParams(_("Unknown high-latitude rule."))
    try:
        ZoneInfo(p.timezone)
    except Exception as e:  # noqa: BLE001 — unknown key or no tz database
        raise InvalidParams(_("Unknown time zone.")) from e
    fajr, isha, delay = p.angles()
    if not (ANGLE_RANGE[0] <= fajr <= ANGLE_RANGE[1]) or (
        isha is not None and not (ANGLE_RANGE[0] <= isha <= ANGLE_RANGE[1])
    ):
        raise InvalidParams(
            _("Angles must be between {low} and {high} degrees.", low=ANGLE_RANGE[0], high=ANGLE_RANGE[1])
        )
    if delay is not None and not (DELAY_RANGE[0] <= delay <= DELAY_RANGE[1]):
        raise InvalidParams(
            _("The Isha delay must be between {low} and {high} minutes.", low=DELAY_RANGE[0], high=DELAY_RANGE[1])
        )
    for k, v in p.offsets.items():
        if k not in OUTPUT or abs(v) > MAX_OFFSET_MIN:
            raise InvalidParams(_("Offsets are whole minutes between -{n} and {n}.", n=MAX_OFFSET_MIN))
    for k, (lo, hi) in p.bounds.items():
        if k not in OUTPUT or any(b and _to_min(b) is None for b in (lo, hi)):
            raise InvalidParams(_("Limits are times of the day (HH:MM)."))
    return p


def _to_min(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m) if 0 <= int(h) < 24 and 0 <= int(m) < 60 else None
    except ValueError:
        return None


def _clamp(value: str, prayer: str, bounds: tuple[str, str]) -> str:
    """`value` held between the mosque's floor and ceiling; Isha after midnight counts as late, not early."""
    v = _to_min(value)
    if v is None:
        return value
    late = prayer == "isha"  # a small hour is past midnight: later than 23:55, not earlier than 19:10
    if late and v < 6 * 60:
        v += 1440
    lo, hi = (_to_min(b) if b else None for b in bounds)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return f"{(v // 60) % 24:02d}:{v % 60:02d}"


def _hhmm(hours: float, offset_min: int) -> str:
    """Hours of the day (float) -> "HH:MM" rounded to the nearest minute; '' when the sun never gets there."""
    if not isinstance(hours, int | float) or math.isnan(hours):
        return ""  # `praytimes` answers a text marker when the sun never reaches the angle
    total = int(math.floor(hours * 60 + 0.5)) + offset_min
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def utc_offset_hours(day: dt.date, tz: str) -> float:
    """Offset from UTC in hours at local noon of `day`, daylight saving included."""
    off = dt.datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo(tz)).utcoffset()
    return (off or dt.timedelta()).total_seconds() / 3600


def day_times(day: dt.date, p: Params) -> dict[str, str]:
    """{"fajr","sunrise","dhuhr","asr","maghrib","isha"} -> "HH:MM" ('' when not computable)."""
    fajr, isha_angle, isha_delay = p.angles()
    settings = {
        "fajr": fajr,
        # a delay is added here: the PyPI port of PrayTimes subtracts a "N min" Isha from maghrib (a sign
        # error against the original), so the library only ever sees an angle
        "isha": isha_angle if isha_angle is not None else 17,
        "maghrib": "0 min",
        "dhuhr": "0 min",
        "imsak": "10 min",
        "midnight": "Standard",
        "asr": ASR[p.asr],
        "highLats": HIGH_LATS[p.high_lats],
    }
    with _LOCK:
        pt = PrayTimes("MWL")
        pt.adjust(settings)
        raw = pt.getTimes((day.year, day.month, day.day), (p.lat, p.lon), utc_offset_hours(day, p.timezone), 0, "Float")
    if isha_delay and isinstance(raw["maghrib"], int | float):
        raw["isha"] = raw["maghrib"] + isha_delay / 60.0
    out = {k: _hhmm(raw[k], int(p.offsets.get(k, 0))) for k in OUTPUT}
    return {k: (_clamp(v, k, p.bounds[k]) if v and k in p.bounds else v) for k, v in out.items()}


def year_times(year: int, p: Params) -> dict[str, dict[str, str]]:
    """{"YYYY-MM-DD": day_times} for every day of `year`."""
    out, d = {}, dt.date(year, 1, 1)
    while d.year == year:
        out[d.isoformat()] = day_times(d, p)
        d += dt.timedelta(days=1)
    return out


def params_from(raw: dict, timezone: str) -> Params:
    """Params from the stored / posted dict (numbers may arrive as text); InvalidParams if unreadable."""
    from .i18n import _

    def num(key, default=None):
        v = raw.get(key)
        if v in (None, ""):
            return default
        try:
            return float(str(v).replace(",", "."))
        except ValueError as e:
            raise InvalidParams(_("A number is expected for {field}.", field=key)) from e

    lat, lon = num("lat"), num("lon")
    if lat is None or lon is None:
        raise InvalidParams(_("Latitude and longitude are required."))
    offsets = {}
    for k, v in (raw.get("offsets") or {}).items():
        if v in (None, "", 0, "0"):
            continue
        try:
            offsets[k] = int(float(str(v)))
        except ValueError as e:
            raise InvalidParams(_("A number is expected for {field}.", field=k)) from e
    bounds = {}
    for k, v in (raw.get("bounds") or {}).items():
        lo, hi = (list(v) + ["", ""])[:2] if isinstance(v, list | tuple) else ("", "")
        if lo or hi:
            bounds[k] = (str(lo or "").strip(), str(hi or "").strip())
    return check(
        Params(
            lat=lat,
            lon=lon,
            timezone=timezone,
            method=str(raw.get("method") or "uoif"),
            fajr_angle=num("fajr_angle"),
            isha_angle=num("isha_angle"),
            isha_delay_min=num("isha_delay_min"),
            asr=str(raw.get("asr") or "standard"),
            high_lats=str(raw.get("high_lats") or "nightmiddle"),
            offsets=offsets,
            bounds=bounds,
        )
    )
