"""Prayer times of the mosque, from the console: the source, the calculation, the typed timetable.

A Mawaqit mosque has nothing to enter here (Sada neither copies nor publishes its timetable). A mosque
without Mawaqit chooses *computed* (coordinates and a method, with a typed correction where it wants)
or *manual* (its own timetable, by month or as a CSV); the public page is then provided.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from ... import mawaqit
from ... import prayer_calc as calc
from ... import prayer_fit as fit
from ...i18n import _
from .. import db, edge, prayer_times
from .. import prayer_export as export
from ..context import CtxDep, Invalid
from .common import _ok

router = APIRouter()


def _invalid(e: Exception) -> Invalid:
    return Invalid(str(e))


def _calc_of(raw) -> dict:
    return raw if isinstance(raw, dict) else {}


def _month(value: str | None, ctx) -> str:
    return (value or "").strip() or prayer_times.local_today(ctx.mosque).strftime("%Y-%m")


@router.put("/settings/prayer")
async def prayer_save(ctx: CtxDep):
    """Choose the source and its options. Raw body: the calculation and the jumu'a times are objects/lists."""
    f = await ctx.raw_body()
    ctx.require("admin")
    source = str(f.get("source") or "").strip()
    jumua = f.get("jumua") if isinstance(f.get("jumua"), list) else []
    try:
        if source == "mawaqit":
            url = prayer_times.normalize_mawaqit(str(f.get("mawaqit_url") or ctx.mosque.get("mawaqit_url") or ""))
            if not url:
                raise ValueError(_("Give the Mawaqit address or identifier of the mosque."))
            db.update_mosque(ctx.conn, ctx.mosque["id"], mawaqit_url=url)
            ctx.mosque["mawaqit_url"] = url
        saved = prayer_times.save(
            ctx.conn,
            ctx.mosque,
            source=source,
            public=str(f.get("public", "1")).strip().lower() not in ("0", "false", "off", ""),
            jumua=[str(t) for t in jumua],
            cfg=_calc_of(f.get("calc")),
        )
    except (ValueError, calc.InvalidParams) as e:
        raise _invalid(e) from e
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "horaires.source",
        "mosque",
        ctx.mosque["id"],
        ip=ctx.ip,
        source=source,
    )
    return _ok(_("Prayer times saved."), prayer=prayer_times.view(ctx.conn, ctx.mosque), source=saved["source"])


@router.post("/settings/prayer/preview")
async def prayer_preview(ctx: CtxDep):
    """The calculated month for settings not saved yet: nothing is written."""
    f = await ctx.raw_body()
    ctx.require("admin")
    month = _month(str(f.get("month") or ""), ctx)
    try:
        days = prayer_times.preview(ctx.mosque, _calc_of(f.get("calc")), month)
    except (ValueError, calc.InvalidParams) as e:
        raise _invalid(e) from e
    return {"month": month, "days": days}


@router.post("/settings/prayer/deduce")
async def prayer_deduce(ctx: CtxDep):
    """Settings that reproduce the mosque's Mawaqit year: a suggestion to preview, nothing is saved,
    and the calendar read from Mawaqit is not kept."""
    f = await ctx.body()
    ctx.require("admin")
    try:
        url = prayer_times.normalize_mawaqit(f.get("mawaqit_url") or ctx.mosque.get("mawaqit_url") or "")
        if not url:
            raise ValueError(_("Give the Mawaqit address or identifier of the mosque."))
        year = prayer_times.local_today(ctx.mosque).year
        pub = mawaqit.fetch_calendar(url)
        found = fit.deduce(pub["calendar"], pub["lat"], pub["lon"], ctx.mosque.get("timezone") or pub["timezone"], year)
    except (ValueError, calc.InvalidParams) as e:
        raise _invalid(e) from e
    except Exception as e:  # noqa: BLE001 — network or a page that changed
        raise Invalid(_("Mawaqit could not be read: {error}", error=e)) from e
    cfg = {**found["params"], "lat": round(pub["lat"], 4), "lon": round(pub["lon"], 4)}
    return {"year": year, "calc": cfg, "errors": found["errors"], "worst": found["worst"]}


def _need_own_timetable(ctx) -> dict:
    st = prayer_times.read(ctx.conn, ctx.mosque)
    if st["source"] not in ("computed", "manual"):
        raise Invalid(_("With Mawaqit there is nothing to enter: choose the calculation or your own timetable first."))
    return st


@router.get("/settings/prayer/days")
def prayer_days(ctx: CtxDep, month: str = ""):
    ctx.require("admin")
    _need_own_timetable(ctx)
    month = _month(month, ctx)
    try:
        return {"month": month, "days": prayer_times.month_view(ctx.conn, ctx.mosque, month)}
    except (ValueError, calc.InvalidParams) as e:
        raise _invalid(e) from e


@router.put("/settings/prayer/days")
async def prayer_days_save(ctx: CtxDep):
    """Replace one month of typed times. Rows are checked all together: one error, nothing is written."""
    f = await ctx.raw_body()
    ctx.require("admin")
    _need_own_timetable(ctx)
    month = _month(str(f.get("month") or ""), ctx)
    try:
        first, last = prayer_times.month_bounds(month)
    except ValueError as e:
        raise _invalid(e) from e
    days: dict[str, dict] = {}
    errors = []
    for row in f.get("days") if isinstance(f.get("days"), list) else []:
        d = str((row or {}).get("date") or "")
        if not (first <= d <= last) or len(d) != 10:
            errors.append(_("{value} is not in {month}.", value=d, month=month))
            continue
        try:
            days[d] = prayer_times.clean_day(row)
        except ValueError as e:
            errors.append(f"{d}: {e}")
    if errors:
        raise Invalid(" ".join(errors[: prayer_times.MAX_ERRORS_SHOWN]))
    n = prayer_times.save_days(ctx.conn, ctx.mosque["id"], days, replace_range=(first, last))
    db.audit(
        ctx.conn,
        ctx.mosque["id"],
        ctx.user["id"],
        "horaires.mois",
        "mosque",
        ctx.mosque["id"],
        ip=ctx.ip,
        mois=month,
        jours=n,
    )
    return _ok(
        _("Timetable of {month} saved: {n} day(s).", month=month, n=n),
        month=month,
        days=prayer_times.month_view(ctx.conn, ctx.mosque, month),
    )


@router.post("/settings/prayer/import")
async def prayer_import(ctx: CtxDep):
    """Import a CSV (a header row naming date, fajr, … isha, optionally <prayer>_iqama). All or nothing."""
    f = await ctx.body()
    ctx.require("admin")
    _need_own_timetable(ctx)
    days, errors = prayer_times.parse_csv(f.get("csv", ""))
    if errors:
        shown = errors[: prayer_times.MAX_ERRORS_SHOWN]
        raise Invalid(" ".join(_("Line {line}: {message}", line=e["line"], message=e["message"]) for e in shown))
    n = prayer_times.save_days(ctx.conn, ctx.mosque["id"], days)
    db.audit(
        ctx.conn, ctx.mosque["id"], ctx.user["id"], "horaires.import", "mosque", ctx.mosque["id"], ip=ctx.ip, jours=n
    )
    return _ok(_("{n} day(s) imported.", n=n), imported=n, first=min(days, default=""), last=max(days, default=""))


@router.get("/settings/prayer/export")
def prayer_export(
    ctx: CtxDep,
    format: str = "csv",  # noqa: A002 — the query parameter is named `format`
    year: int = 0,
    month: str = "",
    start: str = "",
    end: str = "",
    ramadan: bool = False,
):
    """The effective times of a period as a CSV (a calculated period becomes a timetable that can be
    edited) or as a printable PDF calendar. The period: `start`/`end`, `ramadan`, `month` or `year`."""
    ctx.require("admin")
    _need_own_timetable(ctx)
    if format not in export.FORMATS:
        raise Invalid(_("The format is pdf or csv."))
    try:
        first, last = export.period(
            prayer_times.local_today(ctx.mosque), year=year, month=month, start=start, end=end, ramadan_=ramadan
        )
        body = (
            export.pdf_bytes(ctx.conn, ctx.mosque, first, last, edge.public_base(str(ctx.request.base_url)))
            if format == "pdf"
            else export.csv_text(ctx.conn, ctx.mosque, first, last)
        )
    except (ValueError, calc.InvalidParams) as e:  # NoTimes is a ValueError
        raise _invalid(e) from e
    return Response(
        body,
        media_type="application/pdf" if format == "pdf" else "text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export.file_name(first, last, format)}"'},
    )
