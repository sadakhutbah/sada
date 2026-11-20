"""Mawaqit as one source of prayer times, with a local cache (fallback if the site is unreachable).

Nothing outside this module and `backoffice.prayer_times` should know Mawaqit exists: consumers read
the source-neutral shape of `sada.prayers`."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from .i18n import _
from .prayers import finish as _finish

UA = "Mozilla/5.0 (X11; Linux x86_64) sada/0.1"


def _extract_conf(html: str) -> dict:
    m = re.search(r"confData\s*=\s*(\{.*?\});\s*\n", html, re.S)
    if not m:
        raise ValueError(_("confData not found in the Mawaqit page"))
    return json.loads(m.group(1))


def fetch_live(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    conf = _extract_conf(r.text)
    return {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": url,
        "name": conf.get("name"),
        "jumua": conf.get("jumua"),
        "jumua2": conf.get("jumua2"),
        "jumua3": conf.get("jumua3"),
        "times": conf.get("times"),
        "iqama": iqama_times(conf, time.localtime()),
        "timezone": conf.get("timezone"),
    }


def fetch_calendar(url: str, timeout: float = 15.0, get=None) -> dict:
    """The year's published calendar and the mosque's coordinates, to read once (deducing the calculation
    settings of a mosque that leaves Mawaqit). Returned to the caller, never stored."""
    r = (get or requests.get)(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    conf = _extract_conf(r.text)
    cal = conf.get("calendar")
    if not isinstance(cal, list) or len(cal) != 12 or conf.get("latitude") is None or conf.get("longitude") is None:
        raise ValueError(_("The Mawaqit page does not publish a yearly calendar with coordinates."))
    return {
        "calendar": cal,
        "lat": float(conf["latitude"]),
        "lon": float(conf["longitude"]),
        "timezone": conf.get("timezone") or "Europe/Paris",
        "name": conf.get("name"),
    }


def _shift(hhmm: str, minutes: int) -> str:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    total = (h * 60 + m + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def iqama_times(conf: dict, day) -> list[str] | None:
    """Iqama of each prayer of `day` (a time.struct_time or a date), as "HH:MM", from the Mawaqit
    configuration: a fixed iqama wins, otherwise the day's calendar entry (a delay "+10" after the
    adhan, or a time). None when the mosque publishes no iqama: nothing is invented."""
    if conf.get("iqamaEnabled") is False:
        return None
    month, mday = (day.tm_mon, day.tm_mday) if hasattr(day, "tm_mon") else (day.month, day.day)
    times = conf.get("times") or []
    fixed = conf.get("fixedIqama") or []
    cal = conf.get("iqamaCalendar") or []
    try:
        entry = (cal[month - 1] or {}).get(str(mday)) or []
    except (IndexError, AttributeError, TypeError):
        entry = []
    out = []
    for i in range(5):
        value = fixed[i] if i < len(fixed) and fixed[i] else (entry[i] if i < len(entry) else "")
        value = str(value or "").strip()
        try:
            if value.startswith("+") and i < len(times) and times[i]:
                out.append(_shift(times[i], int(value[1:])))
            elif re.fullmatch(r"\d{1,2}:\d{2}", value):
                out.append(_shift(value, 0))
            else:
                out.append("")
        except ValueError:
            out.append("")
    return out if any(out) else None


def resolve(
    url: str,
    cache: dict | None,
    age_s: float,
    max_age_h: float = 20.0,
    timeout: float = 15.0,
    fetch=None,
    now: float | None = None,
):
    """(times, data to store or None) from a cache of any kind (file, database row).

    Fresh cache (recent AND fetched today): used as is. Otherwise Mawaqit is fetched; on failure the
    stale cache is used. The prayer times move every day (and by an hour at the clock change): a
    cache from yesterday is never fresh, however young.
    """
    fetch = fetch or fetch_live
    now = time.time() if now is None else now
    same_day = time.localtime(now - age_s)[:3] == time.localtime(now)[:3]  # cached today, local clock
    if cache and same_day and age_s < max_age_h * 3600:
        cache["origin"] = _("cache (fresh)")
        return _finish(cache), None
    if not url:
        if cache:
            cache["origin"] = _("cache (no Mawaqit URL configured)")
            return _finish(cache), None
        raise ValueError(
            _(
                "no time source: empty Mawaqit URL (--mawaqit-url or Settings → Mosque) "
                "and no local cache; otherwise --times / jumua_times_override"
            )
        )
    try:
        data = fetch(url, timeout)
    except Exception as e:  # network failure / changed format: fall back on the cache
        if cache:
            cache["origin"] = _("cache (stale, live fetch failed: {error})", error=e)
            return _finish(cache), None
        raise
    stored = dict(data)
    data["origin"] = "live"
    return _finish(data), stored


def get_jumua(url: str, cache_path: str, max_age_h: float = 20.0, timeout: float = 15.0) -> dict:
    """Mawaqit data + jumua_list (jumua times) and origin (live / cache), local file cache."""
    cp = Path(cache_path)
    cache = None
    if cp.exists():
        try:
            cache = json.loads(cp.read_text())
        except Exception:
            cache = None
    age = time.time() - cp.stat().st_mtime if cache else 0.0
    data, stored = resolve(url, cache, age, max_age_h, timeout)
    if stored is not None:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(stored, ensure_ascii=False, indent=1))
    return data
