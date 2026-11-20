"""Find the calculation settings that best reproduce a published yearly timetable.

Used to leave a timetable source for Sada's own calculation: give the year's times (one list per
day: fajr, sunrise, dhuhr, asr, maghrib, isha) and the coordinates, get back the method, the high
latitude rule, the Asr school, the per-prayer offsets and the floors/ceilings that come closest, with
the gap in minutes prayer by prayer. The timetable is only read, never kept.

Each prayer depends on its own settings (Fajr on its angle, Isha on its angle or delay, Asr on the
school, the others on nothing), so they are searched one at a time. A published table is often the
calculation plus the mosque's own rules ("Isha never before 19:10"): the days the calculation cannot
explain are therefore read as a floor or a ceiling, not counted against the angle.
"""

from __future__ import annotations

import collections
import datetime as dt
import statistics
from dataclasses import replace

from . import prayer_calc as calc

#: Position of each prayer in a published day (the Mawaqit calendar layout).
ORDER = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")
FAJR_ANGLES = tuple(a / 2 for a in range(16, 45))  # 8 .. 22 degrees
ISHA_ANGLES = tuple(a / 2 for a in range(16, 45))
ISHA_DELAYS = (60.0, 75.0, 90.0, 120.0)
OFFSET_RANGE = range(-15, 16)
MIN_DAYS = 300


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def reference(calendar: list, year: int) -> dict[dt.date, dict[str, int]]:
    """{date: {prayer: minutes}} of the published year; a day or a time that is absent is left out."""
    out: dict[dt.date, dict[str, int]] = {}
    for month, days in enumerate(calendar or [], start=1):
        for key, row in (days or {}).items():
            try:
                day = dt.date(year, month, int(key))
            except ValueError:
                continue  # 29 February of a leap-year table, in a year that has none
            vals = {n: _minutes(t) for n, t in zip(ORDER, row or [], strict=False)}
            vals = {n: v for n, v in vals.items() if v is not None}
            if vals:
                out[day] = vals
    return out


def _gap(a: int, b: int) -> int:
    return (a - b + 720) % 1440 - 720  # across midnight too


def _gaps(ref: dict, got: dict, prayer: str, offset: int) -> list[int]:
    out = []
    for day, vals in ref.items():
        g, r = _minutes(got.get(day.isoformat(), {}).get(prayer, "")), vals.get(prayer)
        if g is not None and r is not None:
            out.append(_gap(g + offset, r))
    return out


def _rank(gaps: list[int], offset: int) -> tuple:
    """Smaller is better: most days within one minute, then the smallest average gap, then the
    smallest offset (an offset that is not needed is not suggested)."""
    if not gaps:
        return (1, 0, 0, 0)
    near = [abs(g) for g in gaps if abs(g) <= 1]
    return (-len(near) / len(gaps), statistics.fmean(abs(g) for g in gaps if abs(g) <= 3) if near else 99, abs(offset))


def _best_offset(ref: dict, got: dict, prayer: str) -> tuple[int, list[int]]:
    best_rank, best = None, (0, [])
    for off in OFFSET_RANGE:
        gaps = _gaps(ref, got, prayer, off)
        rank = _rank(gaps, off)
        if best_rank is None or rank < best_rank:
            best_rank, best = rank, (off, gaps)
    return best


def _stats(gaps: list[int]) -> dict:
    if not gaps:
        return {"days": 0, "max": None, "mean": None, "within1": None}
    return {
        "days": len(gaps),
        "max": max(abs(g) for g in gaps),
        "mean": round(statistics.fmean(abs(g) for g in gaps), 2),
        "within1": round(100 * sum(abs(g) <= 1 for g in gaps) / len(gaps), 1),
    }


def _fmt(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _bound(ref: dict, got: dict, prayer: str, offset: int) -> tuple[str, str]:
    """Floor and ceiling that explain the days the calculation misses: the published value most
    often repeated where it is later (floor) or earlier (ceiling) than the calculation."""
    late = prayer == "isha"
    up, down = collections.Counter(), collections.Counter()
    for day, vals in ref.items():
        g, r = _minutes(got.get(day.isoformat(), {}).get(prayer, "")), vals.get(prayer)
        if g is None or r is None:
            continue
        g += offset
        if late and g < 360:
            g += 1440
        if late and r < 360:
            r += 1440
        if r - g > 1:
            up[r] += 1
        elif g - r > 1:
            down[r] += 1
    floor = up.most_common(1)[0] if up else None
    ceil = down.most_common(1)[0] if down else None
    # a value repeated on at least three days is a rule; a lone difference is a typed correction
    return (_fmt(floor[0]) if floor and floor[1] >= 3 else "", _fmt(ceil[0]) if ceil and ceil[1] >= 3 else "")


def deduce(calendar: list, lat: float, lon: float, timezone: str, year: int) -> dict:
    """Best settings for a published year: {"params", "errors": {prayer: stats}, "worst"}.

    A suggestion to preview, not a decision: a timetable typed by hand is matched only as closely as
    the calculation allows, and `errors` says how closely.
    """
    ref = reference(calendar, year)
    if len(ref) < MIN_DAYS:
        raise calc.InvalidParams("a full year is needed to deduce the settings")
    base = calc.Params(lat=lat, lon=lon, timezone=timezone, method="custom")

    def year_of(p):
        return calc.year_times(year, p)

    plain = year_of(base)
    offsets: dict[str, int] = {}
    chosen: dict[str, dict] = {}  # prayer -> the calculated year it was matched on
    for prayer in ("sunrise", "dhuhr", "maghrib"):
        offsets[prayer], _g = _best_offset(ref, plain, prayer)
        chosen[prayer] = plain

    best_asr = None
    for school in calc.ASR:
        got = year_of(replace(base, asr=school))
        off, gaps = _best_offset(ref, got, "asr")
        if best_asr is None or _rank(gaps, off) < best_asr[0]:
            best_asr = (_rank(gaps, off), school, off, got)
    assert best_asr is not None
    asr_school, offsets["asr"], chosen["asr"] = best_asr[1], best_asr[2], best_asr[3]

    fajr_best = isha_best = None  # (rank, angle, rule, offset, delay, year)

    def search(prayer: str, angles: tuple, field_: str):
        """Every angle under one rule, then the best three under every rule: the rule only matters
        where the sun does not reach the angle, so most of the grid needs one pass."""
        scored = []
        for angle in angles:
            got = year_of(replace(base, **{field_: angle}, high_lats="nightmiddle"))
            off, gaps = _best_offset(ref, got, prayer)
            scored.append((_rank(gaps, off), angle))
        best = None
        for _r, angle in sorted(scored)[:3]:
            for rule in calc.HIGH_LATS:
                got = year_of(replace(base, **{field_: angle}, high_lats=rule))
                off, gaps = _best_offset(ref, got, prayer)
                if best is None or _rank(gaps, off) < best[0]:
                    best = (_rank(gaps, off), angle, rule, off, None, got)
        return best

    fajr_best = search("fajr", FAJR_ANGLES, "fajr_angle")
    isha_best = search("isha", ISHA_ANGLES, "isha_angle")
    for delay in ISHA_DELAYS:
        got = year_of(replace(base, isha_delay_min=delay))
        off, gaps = _best_offset(ref, got, "isha")
        if isha_best is None or _rank(gaps, off) < isha_best[0]:
            isha_best = (_rank(gaps, off), None, "nightmiddle", off, delay, got)
    assert fajr_best is not None and isha_best is not None
    offsets["fajr"], chosen["fajr"] = fajr_best[3], fajr_best[5]
    offsets["isha"], chosen["isha"] = isha_best[3], isha_best[5]

    bounds = {}
    for prayer in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        lo, hi = _bound(ref, chosen[prayer], prayer, offsets[prayer])
        if lo or hi:
            bounds[prayer] = (lo, hi)

    params = {
        "method": "custom",
        "fajr_angle": fajr_best[1],
        "isha_angle": isha_best[1],
        "isha_delay_min": isha_best[4],
        "asr": asr_school,
        "high_lats": fajr_best[2],
        "offsets": {k: v for k, v in offsets.items() if v},
        "bounds": {k: list(v) for k, v in bounds.items()},
    }
    for key, (fa, ia, delay) in calc.METHODS.items():
        if key != "custom" and (fa, ia, delay) == (
            params["fajr_angle"],
            params["isha_angle"],
            params["isha_delay_min"],
        ):
            params["method"] = key  # the mosque sees "UOIF", not 12/12
            break

    final = year_of(
        calc.Params(
            lat=lat,
            lon=lon,
            timezone=timezone,
            method=params["method"],
            fajr_angle=params["fajr_angle"],
            isha_angle=params["isha_angle"],
            isha_delay_min=params["isha_delay_min"],
            asr=asr_school,
            high_lats=params["high_lats"],
            offsets=offsets,
            bounds=bounds,
        )
    )
    errors = {p: _stats(_gaps(ref, final, p, 0)) for p in ORDER}
    return {"params": params, "errors": errors, "worst": max((e["max"] or 0) for e in errors.values())}
