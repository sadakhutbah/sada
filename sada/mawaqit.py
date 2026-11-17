"""Jumu'a times from Mawaqit, with a local cache (fallback if the site is unreachable)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from .i18n import _

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
        "timezone": conf.get("timezone"),
    }


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


def from_backoffice(backoffice_url: str, api_port: int, fetch=None, log=None) -> dict | None:
    """Times of the calling orchestrator's mosque, cached by the back office; None if unreachable."""
    log = log or (lambda _m: None)
    if not backoffice_url:
        return None
    import urllib.request
    from urllib.parse import urlencode

    from .backoffice.internal import HEADER, token

    def _get(u: str, tok: str) -> dict:  # pragma: no cover (network)
        req = urllib.request.Request(u, headers={HEADER: tok})
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 — configured back office
            return json.loads(r.read(1 << 20))

    u = backoffice_url.rstrip("/") + "/api/v1/internal/times?" + urlencode({"port": api_port or 0})
    try:
        d = (fetch or _get)(u, token()) or {}
    except Exception as e:  # noqa: BLE001
        log(_("Times: back office unreachable ({error}), local cache used", error=e))
        return None
    t = d.get("times")
    return _finish(t) if isinstance(t, dict) else None


def prayer_times(d: dict) -> dict:
    """Times of the day from Mawaqit: {"fajr","dhuhr","asr","maghrib","isha"} ("HH:MM" strings).

    Mawaqit publishes `times` = [fajr, dhuhr, asr, maghrib, isha]; a shorter array gives
    only what is present, without inventing anything.
    """
    names = ("fajr", "dhuhr", "asr", "maghrib", "isha")
    raw = d.get("times") or []
    return {n: t for n, t in zip(names, raw, strict=False) if t}


def _finish(d: dict) -> dict:
    d["jumua_list"] = [t for t in (d.get("jumua"), d.get("jumua2"), d.get("jumua3")) if t]
    return d
