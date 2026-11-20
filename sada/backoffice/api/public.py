"""Public, read-only endpoints: no session, no token, nothing private.

`GET /api/v1/public/mosques/{slug}/prayer-times` serves the prayer times of a mosque that publishes
them (source *computed* or *manual*): today by default, `?date=YYYY-MM-DD` for a day, `?month=YYYY-MM`
for a month. A Mawaqit mosque is not hosted here: the answer is 404 and says where its page is. The
answer carries the times only (no address, no identifier, no setting), may be read from any web page
(CORS), is cached for a minute, and is limited per client.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import prayer_calc as calc
from ...i18n import _
from .. import orm, prayer_times
from ..context import ConnDep
from ..models import Mosque
from ..throttle import PUBLIC_CACHE, SlidingWindow

router = APIRouter()

RATE_PER_MINUTE = 60
MAX_AGE_S = 60

_limiter = SlidingWindow(RATE_PER_MINUTE, 60.0)
_cache = PUBLIC_CACHE


def reset() -> None:
    """For tests: forget the cache and the counters."""
    global _limiter
    _limiter = SlidingWindow(RATE_PER_MINUTE, 60.0)
    _cache.clear()


def _json(body: dict, status: int = 200, cache: bool = False, **headers) -> JSONResponse:
    resp = JSONResponse(body, status_code=status)
    resp.headers["Access-Control-Allow-Origin"] = "*"  # read-only public data: any page may show it
    resp.headers["Cache-Control"] = f"public, max-age={MAX_AGE_S}" if cache else "no-store"
    for k, v in headers.items():
        resp.headers[k.replace("_", "-")] = str(v)
    return resp


@router.get("/public/mosques/{slug}/prayer-times")
def prayer_times_public(slug: str, request: Request, conn: ConnDep, date: str = "", month: str = ""):
    client = request.client.host if request.client else "?"
    wait = _limiter.hit(client)
    if wait:
        return _json({"error": _("too many requests")}, 429, Retry_After=int(wait) + 1)
    key = (slug, date, month, dt.date.today().isoformat())
    hit = _cache.get(key)
    if hit is not None:
        return _json(hit[1], hit[0], cache=hit[0] == 200)
    status, body = _build(conn, slug, date, month)
    _cache.put(key, (status, body))
    return _json(body, status, cache=status == 200)


def _build(conn, slug: str, date: str, month: str) -> tuple[int, dict]:
    mosque = orm.first(conn, Mosque, slug=slug)
    if not mosque:
        return 404, {"error": _("not found")}
    st = prayer_times.read(conn, mosque)
    if st["source"] == "mawaqit":
        # Not hosted here: send the reader to the mosque's own page.
        return 404, {
            "error": _("not published here"),
            "source": "mawaqit",
            "mawaqit_url": mosque.get("mawaqit_url") or "",
        }
    if st["source"] not in ("computed", "manual") or not st["public"]:
        return 404, {"error": _("not found")}
    try:
        if month:
            prayer_times.month_bounds(month)
            return 200, prayer_times.public_payload(conn, mosque, month=month)
        day = dt.date.fromisoformat(date) if date else None
        return 200, prayer_times.public_payload(conn, mosque, day=day)
    except (ValueError, calc.InvalidParams):
        return 422, {"error": _("refused"), "message": _("Use ?date=YYYY-MM-DD or ?month=YYYY-MM.")}
