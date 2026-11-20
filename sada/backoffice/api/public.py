"""Public, read-only endpoints: no session, no token, nothing private.

`GET /api/v1/public/mosques/{slug}/prayer-times` serves the prayer times of a mosque that publishes
them (source *computed* or *manual*): today by default, `?date=YYYY-MM-DD` for a day, `?month=YYYY-MM`
for a month. A Mawaqit mosque is not hosted here: the answer is 404 and says where its page is. The
answer carries the times only (no address, no identifier, no setting), may be read from any web page
(CORS), is cached for a minute, and is limited per client.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ... import prayer_calc as calc
from ...i18n import _
from .. import orm, prayer_times, public_page
from .. import prayer_export as export
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


def _published(conn, slug: str):
    """(mosque, None) for a mosque that publishes its own times, else (None, (status, body))."""
    mosque = orm.first(conn, Mosque, slug=slug)
    if not mosque:
        return None, (404, {"error": _("not found")})
    st = prayer_times.read(conn, mosque)
    if st["source"] == "mawaqit":
        return None, (
            404,
            {"error": _("not published here"), "source": "mawaqit", "mawaqit_url": mosque.get("mawaqit_url") or ""},
        )
    if st["source"] not in ("computed", "manual") or not st["public"]:
        return None, (404, {"error": _("not found")})
    return mosque, None


@router.get("/public/mosques/{slug}/prayer-times/export")
def prayer_times_export(
    slug: str,
    request: Request,
    conn: ConnDep,
    format: str = "pdf",  # noqa: A002 — the query parameter is named `format`
    month: str = "",
    start: str = "",
    end: str = "",
    ramadan: bool = False,
):
    """A printable calendar (PDF) or a CSV of a month or a period (`start`/`end`, `ramadan`), at most a year."""
    client = request.client.host if request.client else "?"
    wait = _limiter.hit(client)
    if wait:
        return _json({"error": _("too many requests")}, 429, Retry_After=int(wait) + 1)
    mosque, refused = _published(conn, slug)
    if refused:
        return _json(refused[1], refused[0])
    if format not in export.FORMATS:
        return _json({"error": _("refused"), "message": _("The format is pdf or csv.")}, 422)
    try:
        first, last = export.period(
            prayer_times.local_today(mosque), month=month or ("" if (start or end or ramadan) else _this_month(mosque)),
            start=start, end=end, ramadan_=ramadan,
        )  # fmt: skip
        body = (
            export.pdf_bytes(conn, mosque, first, last)
            if format == "pdf"
            else export.csv_text(conn, mosque, first, last)
        )
    except (ValueError, calc.InvalidParams) as e:  # NoTimes is a ValueError
        return _json({"error": _("refused"), "message": str(e)}, 422)
    return Response(
        body,
        media_type="application/pdf" if format == "pdf" else "text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{export.file_name(first, last, format)}"',
            "Cache-Control": f"public, max-age={MAX_AGE_S}",
            "Vary": "Accept-Language, X-Sada-Lang",
        },
    )


def _this_month(mosque: dict) -> str:
    return prayer_times.local_today(mosque).strftime("%Y-%m")


@router.get("/public/mosques/{slug}/prayer-times/page")
def prayer_times_page(slug: str, request: Request, conn: ConnDep, month: str = ""):
    """The public page of the timetable: a month in a table, with the PDF and CSV downloads."""
    client = request.client.host if request.client else "?"
    wait = _limiter.hit(client)
    if wait:
        return _json({"error": _("too many requests")}, 429, Retry_After=int(wait) + 1)
    mosque, refused = _published(conn, slug)
    if refused:
        status, body = refused
        return HTMLResponse(
            public_page.refused(body, status), status_code=status, headers={"Cache-Control": "no-store"}
        )
    month = month or _this_month(mosque)
    try:
        first, last = export.period(prayer_times.local_today(mosque), month=month)
        rows = export.days(conn, mosque, first, last)
    except export.NoTimes:
        rows = []
    except (ValueError, calc.InvalidParams):
        return HTMLResponse(
            public_page.refused({"error": _("refused"), "message": _("A month looks like 2027-03.")}, 422),
            status_code=422,
            headers={"Cache-Control": "no-store"},
        )
    page = public_page.render(slug, mosque, month, rows, prayer_times.read(conn, mosque))
    return HTMLResponse(
        page, headers={"Cache-Control": f"public, max-age={MAX_AGE_S}", "Vary": "Accept-Language, X-Sada-Lang"}
    )


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
