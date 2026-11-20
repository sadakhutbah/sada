"""Printable prayer-time calendars (PDF) and CSV of a mosque, for a month or any period.

Only the mosque's own timetable is exported (source *computed* or *manual*). A Mawaqit mosque has
none: the internal cache keeps today's times only, never a calendar, so there is nothing to print and
nothing is copied (`NoTimes`).

The PDF is drawn with ReportLab (BSD) and the Hijri date comes from `hijridate` (Umm al-Qura). One A4
portrait page per calendar month of the period: the mosque's name, the month in both calendars, one
line per day, the Friday line highlighted with the jumu'a time(s). The built-in Helvetica carries
Latin text: a label whose translation cannot be written with it (Arabic) stays in English, and
characters it lacks in a mosque's name become "?".
"""

from __future__ import annotations

import datetime as dt
import io

from hijridate import Gregorian, Hijri
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from ..i18n import _, translate
from . import prayer_times as pt
from .prayer_times import NoTimes

MAX_DAYS = 366
FORMATS = ("pdf", "csv")
RAMADAN = 9  # month number in the Hijri calendar
MIN_FONT = 6.5  # the smallest size a cell is shrunk to

# Each name is a translation key (English source): the weekday and month names are the reader's.
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip

INK = colors.HexColor("#1f2a24")
LEAF = colors.HexColor("#2f6b4f")
GOLD = colors.HexColor("#f3e3b3")
BAND = colors.HexColor("#f4f1e8")
LINE = colors.HexColor("#cfc6b0")
MUTED = colors.HexColor("#6b6f66")


def _latin(text: str) -> str:
    """Text Helvetica can draw (Latin-1 / WinAnsi); anything else is replaced by '?'."""
    return str(text).encode("cp1252", "replace").decode("cp1252")


def label(source: str, **kw) -> str:
    """The label in the reader's language, or its English source when that cannot be drawn."""
    text = _(source, **kw)
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        text = translate(source, "en", **kw)
    return text


# ------------------------------------------------------------------ period


def _date(h: Hijri) -> dt.date:
    return dt.date(*h.to_gregorian().datetuple())


def ramadan(today: dt.date) -> tuple[dt.date, dt.date]:
    """The Ramadan under way, else the next one: (first day, last day) in the Gregorian calendar."""
    year = Gregorian.fromdate(today).to_hijri().year
    for y in (year, year + 1):
        first = _date(Hijri(y, RAMADAN, 1))
        last = _date(Hijri(y, RAMADAN + 1, 1)) - dt.timedelta(days=1)
        if last >= today:
            return first, last
    raise ValueError(_("A date looks like 2027-03-21."))  # pragma: no cover (the library covers 1900-2100)


def _day(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(_("A date looks like 2027-03-21.")) from e


def period(
    today: dt.date, *, year: int = 0, month: str = "", start: str = "", end: str = "", ramadan_: bool = False
) -> tuple[dt.date, dt.date]:
    """The (first, last) day asked for: a range, Ramadan, a month or a year. Raises ValueError (readable)."""
    if start or end:
        first, last = _day(start or end), _day(end or start)
    elif ramadan_:
        first, last = ramadan(today)
    elif month:
        a, b = pt.month_bounds(month)
        first, last = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    else:
        year = year or today.year
        if not 2000 <= year <= 2100:
            raise ValueError(_("A year looks like 2027."))
        first, last = dt.date(year, 1, 1), dt.date(year, 12, 31)
    if last < first:
        raise ValueError(_("The end of the period is before its start."))
    if (last - first).days + 1 > MAX_DAYS:
        raise ValueError(_("At most {n} days at a time.", n=MAX_DAYS))
    if not (2000 <= first.year and last.year <= 2100):
        raise ValueError(_("A year looks like 2027."))
    return first, last


def file_name(first: dt.date, last: dt.date, fmt: str) -> str:
    """prayer-times-2027-03.pdf for a whole month, -2027.csv for a year, else from_to."""
    month_end = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    if first.day == 1 and last == month_end:
        span = first.strftime("%Y-%m")
    elif (first.month, first.day) == (1, 1) and last == dt.date(first.year, 12, 31):
        span = str(first.year)
    else:
        span = f"{first.isoformat()}_{last.isoformat()}"
    return f"prayer-times-{span}.{fmt}"


# ------------------------------------------------------------------ rows


def hijri_text(day: dt.date) -> str:
    """ "12 Ramadan 1448" (the month's name is transliterated, the same in every language)."""
    h = Gregorian.fromdate(day).to_hijri()
    return f"{h.day} {h.month_name()} {h.year}"


def _own_source(conn, mosque: dict) -> dict:
    st = pt.read(conn, mosque)
    if st["source"] not in ("computed", "manual"):
        raise NoTimes(_("With Mawaqit there is nothing to enter: choose the calculation or your own timetable first."))
    return st


def days(conn, mosque: dict, first: dt.date, last: dt.date) -> list[dict]:
    """One entry per day of the period that has something to show: date, Hijri date, the times, jumu'a."""
    st = _own_source(conn, mosque)
    try:
        found = list(pt.effective_days(conn, mosque, first, last, st))
    except pt.calc.InvalidParams as e:
        raise NoTimes(str(e)) from e
    if not found:
        raise NoTimes(_("No prayer time entered for this period."))
    return [
        {
            "date": d,
            "hijri": hijri_text(d),
            "times": {k: f.get(k, "") for k in pt.FIELDS},
            "jumua": list(st["jumua"]) if d.weekday() == 4 else [],
        }
        for d, f in found
    ]


def csv_text(conn, mosque: dict, first: dt.date, last: dt.date) -> str:
    """The CSV of the period: the columns of the import, so the file can be taken back as it is."""
    _own_source(conn, mosque)
    try:
        return pt.export_csv_range(conn, mosque, first, last)
    except pt.calc.InvalidParams as e:
        raise NoTimes(str(e)) from e


# ------------------------------------------------------------------ PDF

_W, _H = A4
_COLS = (  # (header label, width in mm, alignment)
    ("Date", 30, "left"),
    ("Hijri", 27, "left"),
    ("Fajr", 17, "center"),
    ("Sunrise", 18, "center"),
    ("Dhuhr", 34, "center"),
    ("Asr", 17, "center"),
    ("Maghrib", 19, "center"),
    ("Isha", 17, "center"),
)
_TABLE_TOP = _H - 40 * mm


def hijri_span(rows: list[dict]) -> str:
    a = Gregorian.fromdate(rows[0]["date"]).to_hijri()
    b = Gregorian.fromdate(rows[-1]["date"]).to_hijri()
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.month_name()} {a.year}"
    if a.year == b.year:
        return f"{a.month_name()} - {b.month_name()} {a.year}"
    return f"{a.month_name()} {a.year} - {b.month_name()} {b.year}"


def _put(c: canvas.Canvas, x: float, w: float, y: float, text: str, align: str, font: str, size: float) -> None:
    """Write `text` in the cell, a little smaller if it would not fit (several jumu'a times, long names)."""
    room = w - 4 * mm
    while size > MIN_FONT and stringWidth(text, font, size) > room:
        size -= 0.5
    c.setFont(font, size)
    if align == "center":
        c.drawCentredString(x + w / 2, y, text)
    else:
        c.drawString(x + 2 * mm, y, text)


def _page(c: canvas.Canvas, mosque: dict, st: dict, month_rows: list[dict]) -> None:
    first = month_rows[0]["date"]
    widths = [w * mm for _k, w, _a in _COLS]
    total = sum(widths)
    left = (_W - total) / 2

    c.setFillColor(LEAF)
    c.rect(0, _H - 34 * mm, _W, 34 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(_W / 2, _H - 14 * mm, _latin(mosque.get("name") or "")[:60])
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(_W / 2, _H - 22 * mm, f"{label(MONTHS[first.month - 1])} {first.year}")
    c.setFont("Helvetica", 10.5)
    c.drawCentredString(_W / 2, _H - 28 * mm, hijri_span(month_rows))

    head_h = 8 * mm
    y = _TABLE_TOP
    row_h = min(8.2 * mm, (y - head_h - 24 * mm) / len(month_rows))
    c.setFillColor(INK)
    c.rect(left, y - head_h, total, head_h, stroke=0, fill=1)
    c.setFillColor(colors.white)
    x = left
    for (key, _w, align), w in zip(_COLS, widths, strict=True):
        _put(c, x, w, y - head_h + 2.6 * mm, label(key), align, "Helvetica-Bold", 9.5)
        x += w
    y -= head_h

    jumua_word = label("Jumu'a")
    for i, r in enumerate(month_rows):
        friday = r["date"].weekday() == 4
        c.setFillColor(GOLD if friday else (BAND if i % 2 else colors.white))
        c.rect(left, y - row_h, total, row_h, stroke=0, fill=1)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.3)
        c.line(left, y - row_h, left + total, y - row_h)
        base = y - row_h + (row_h - 3.3 * mm) / 2 + 0.4 * mm
        h = Gregorian.fromdate(r["date"]).to_hijri()
        dhuhr = " / ".join(r["jumua"]) if friday and r["jumua"] else r["times"]["dhuhr"]
        cells = [
            f"{label(WEEKDAYS[r['date'].weekday()])} {r['date'].day}" + (f"  {jumua_word}" if friday else ""),
            f"{h.day} {h.month_name()}",
            r["times"]["fajr"],
            r["times"]["sunrise"],
            dhuhr,
            r["times"]["asr"],
            r["times"]["maghrib"],
            r["times"]["isha"],
        ]
        x = left
        for (_key, _w, align), w, text in zip(_COLS, widths, cells, strict=True):
            c.setFillColor(INK if text else MUTED)
            font = "Helvetica-Bold" if friday else "Helvetica"
            _put(c, x, w, base, text or "-", align, font, 9.5 if align == "left" else 10.5)
            x += w
        y -= row_h
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(left, y, total, _TABLE_TOP - y, stroke=1, fill=0)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 8.5)
    note = label("Calculated prayer times.") if st["source"] == "computed" else label("Times set by the mosque.")
    c.drawCentredString(_W / 2, 14 * mm, note)
    c.drawCentredString(_W / 2, 10 * mm, "Sada")


def pdf_bytes(conn, mosque: dict, first: dt.date, last: dt.date) -> bytes:
    """The calendar of the period, one A4 portrait page per calendar month."""
    rows = days(conn, mosque, first, last)
    st = pt.read(conn, mosque)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, invariant=1)
    c.setTitle(_latin(f"{mosque.get('name') or ''} - {first.isoformat()} / {last.isoformat()}"))
    c.setCreator("Sada")
    months: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        months.setdefault((r["date"].year, r["date"].month), []).append(r)
    for month_rows in months.values():
        _page(c, mosque, st, month_rows)
        c.showPage()
    c.save()
    return buf.getvalue()
