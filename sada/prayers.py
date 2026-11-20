"""Prayer times as the rest of Sada reads them, whatever their source.

The source (Mawaqit, a calculation from the mosque's coordinates, or its own timetable) is chosen per
mosque in the back office and resolved there (`backoffice.prayer_times.times`). Everything that
consumes times — the orchestrator that arms the live, the programme, the tarawih, the screens —
speaks this one shape and never names a source:

    {"times": [fajr, dhuhr, asr, maghrib, isha], "iqama": [five "HH:MM"] | None, "sunrise": "HH:MM",
     "jumua_list": ["HH:MM", ...], "origin": "<how they were obtained>", "name": "<mosque>"}

Anything not published is absent or empty: nothing is invented.
"""

from __future__ import annotations

import json

from .i18n import _

NAMES = ("fajr", "dhuhr", "asr", "maghrib", "isha")


def finish(d: dict) -> dict:
    """Complete a source's data with `jumua_list` (the jumu'a times, in order)."""
    if not isinstance(d.get("jumua_list"), list):
        d["jumua_list"] = [t for t in (d.get("jumua"), d.get("jumua2"), d.get("jumua3")) if t]
    return d


def from_backoffice(backoffice_url: str, api_port: int, fetch=None, log=None) -> dict | None:
    """Times of the calling orchestrator's mosque, resolved by the back office; None if unreachable."""
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
    return finish(t) if isinstance(t, dict) else None


def prayer_times(d: dict) -> dict:
    """Times of the day: {"fajr","dhuhr","asr","maghrib","isha"} ("HH:MM" strings), plus
    "isha_iqama" when the mosque publishes its iqama.

    A source publishes `times` = [fajr, dhuhr, asr, maghrib, isha]; a shorter array gives only what
    is present, without inventing anything.
    """
    raw = d.get("times") or []
    out = {n: t for n, t in zip(NAMES, raw, strict=False) if t}
    iqama = d.get("iqama") or []
    if len(iqama) >= 5 and iqama[4]:
        out["isha_iqama"] = iqama[4]  # start of the isha prayer itself (tarawih arm after its end)
    return out
