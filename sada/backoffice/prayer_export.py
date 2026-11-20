"""Printable prayer-time calendars (PDF) and CSV of a mosque, for a month or any period.

Only the mosque's own timetable is exported (source *computed* or *manual*). A Mawaqit mosque has
none: the internal cache keeps today's times only, never a calendar, so there is nothing to print and
nothing is copied (`NoTimes`).

The PDF is drawn with ReportLab (BSD) and the Hijri date comes from `hijridate` (Umm al-Qura). One A4
portrait page per calendar month of the period (a whole Hijri month, Ramadan, is one page): the Sada
wordmark, the mosque's name, the period in both calendars, rows that fill the page, the Friday line
with the jumu'a time(s), the odd nights of Ramadan, a footer with a QR code to the public page. The
The colours and fonts are the front's. The wording is the mosque's, not the reader's: French by
default, from the tables of `prayer_labels` (the Arabic names of the prayers are printed under the
column titles, shaped and set right to left). The Latin fonts cannot draw an Arabic mosque's name:
the characters they lack become "?".
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
from pathlib import Path
from urllib.parse import quote

import arabic_reshaper
from bidi.algorithm import get_display
from hijridate import Gregorian, Hijri
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.pdfgen.pathobject import PDFPathObject

from ..i18n import _, translate
from . import prayer_times as pt
from .prayer_labels import DEFAULT_LANG, PRAYER_AR, labels
from .prayer_times import NoTimes

MAX_DAYS = 366
FORMATS = ("pdf", "csv")
RAMADAN = 9  # month number in the Hijri calendar
MIN_FONT = 6.5  # the smallest size a cell is shrunk to
FILL_NON_ZERO = 1  # the fill rule of an SVG path (ReportLab's constant of the same name)

# Each name is a translation key (English source): the weekday and month names are the reader's.
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip

# The charter of the front (web/src design tokens): paper, ink, gold leaf and the greens.
INK = colors.HexColor("#12261e")  # --color-ink
DEEPER = colors.HexColor("#12261e")  # --color-deeper
DEEP = colors.HexColor("#1c3a2e")  # --color-deep
GREEN = colors.HexColor("#3d6b57")  # --color-green
LEAF = colors.HexColor("#e2c27a")  # --color-leaf (gold)
LEAF2 = colors.HexColor("#cfa95a")  # --color-leaf-2
BAND = colors.HexColor("#f7f3ea")  # --color-paper: the discreet alternate row
LINE = colors.HexColor("#ded5c1")  # --color-line
MUTED = colors.HexColor("#5e6b63")  # --color-muted
IVORY = colors.HexColor("#f0ead9")  # --color-night-ink
NIGHT_MUTED = colors.HexColor("#b3b8a6")  # --color-night-muted
FRIDAY = colors.Color(0.961, 0.918, 0.808)  # the gold leaf at 35 % on white
ODDNIGHT = colors.Color(0.894, 0.929, 0.906)  # the green at 15 % on white


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


def hijri_month(h: Hijri, lang: str | None = None) -> str:
    """The name of a Hijri month: transliterated (the library's) or, with `lang`, from the label table."""
    return labels(lang)["hijri_months"][h.month - 1] if lang else h.month_name()


def hijri_text(day: dt.date, lang: str | None = None) -> str:
    """ "12 Ramadan 1448"; without `lang` the month's name is the transliterated one of the library."""
    h = Gregorian.fromdate(day).to_hijri()
    return f"{h.day} {hijri_month(h, lang)} {h.year}"


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
_MARGIN = 14 * mm
_HEADER_H = 50 * mm
_FOOTER_H = 30 * mm
_LEGEND_H = 13 * mm
_HEAD_ROW = 13 * mm  # the column title and, under it, the Arabic name of the prayer
_MAX_ROW = 12 * mm
_MONTH_ROW = 6.5 * mm  # the band naming the new month when a page spans two (Ramadan: February, then March)
_COLS = (  # (column key of the label table, width in mm, alignment)
    ("date", 27, "left"),
    ("hijri", 46, "left"),
    ("fajr", 16, "center"),
    ("sunrise", 19, "center"),
    ("dhuhr", 24, "center"),
    ("asr", 16, "center"),
    ("maghrib", 19, "center"),
    ("isha", 15, "center"),
)
ODD_NIGHT_EVES = (20, 22, 24, 26, 28)  # the odd nights 21..29 begin at Maghrib of the day before
LAYLAT_AL_QADR_EVE = 26  # the night of the 27th

_FONTS = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
_LOGO = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "sada-mark-or.svg"
_SITE = "sadakhutbah.org"
_fonts: dict[str, str] | None = None


def _fontset() -> dict[str, str]:
    """The charter's fonts (Source Sans 3 for the text, EB Garamond for the titles), registered once.

    They are the files of `assets/fonts`; if one is missing the built-in Helvetica/Times stand in.
    """
    global _fonts
    if _fonts is None:
        names = {
            "sans": ("SadaSans", "SourceSans3-Regular.ttf", "Helvetica"),
            "sans-bold": ("SadaSans-Semibold", "SourceSans3-Semibold.ttf", "Helvetica-Bold"),
            "serif": ("SadaSerif", "EBGaramond-SemiBold.ttf", "Times-Bold"),
            "arabic": ("SadaArabic", "NotoNaskhArabic-SemiBold.ttf", ""),  # no built-in font draws Arabic
        }
        found = {}
        for key, (name, file, fallback) in names.items():
            try:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(_FONTS / file)))
                found[key] = name
            except Exception:  # missing or unreadable file: the built-in font keeps the page printable
                found[key] = fallback
        _fonts = found
    return _fonts


def hijri_span(rows: list[dict], lang: str | None = None) -> str:
    a = Gregorian.fromdate(rows[0]["date"]).to_hijri()
    b = Gregorian.fromdate(rows[-1]["date"]).to_hijri()
    ma, mb = hijri_month(a, lang), hijri_month(b, lang)
    if (a.year, a.month) == (b.year, b.month):
        return f"{ma} {a.year}"
    if a.year == b.year:
        return f"{ma} - {mb} {a.year}"
    return f"{ma} {a.year} - {mb} {b.year}"


def arabic_visual(text: str) -> str:
    """Arabic text ready to be drawn glyph by glyph: letters joined (shaping), then put in visual order (RTL)."""
    return get_display(arabic_reshaper.reshape(text))


# The wordmark is the one of assets/logo (a single path of M L H V Q Z commands), drawn as a vector.
_TOKEN = re.compile(r"[MLHVQCZ]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?")


def _logo_path():
    svg = _LOGO.read_text(encoding="utf-8")
    d = re.search(r' d="([^"]+)"', svg).group(1)
    sx = float(re.search(r"scale\(([\d.]+)\)", svg).group(1))
    tx, ty = (float(v) for v in re.search(r"translate\(([-\d.]+) ([-\d.]+)\)", svg).groups())
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    toks = _TOKEN.findall(d)

    def pt(x: float, y: float) -> tuple[float, float]:  # font units (y up) to the viewBox (y down)
        return tx + sx * x, ty + sx * y

    p = PDFPathObject()
    i, cmd, cur, start = 0, "", (0.0, 0.0), (0.0, 0.0)

    def num() -> float:
        nonlocal i
        i += 1
        return float(toks[i - 1])

    while i < len(toks):
        if toks[i] in "MLHVQCZ" and toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd == "Z":
                p.close()
                cur = start
                continue
        if cmd == "M":
            cur = (num(), num())
            start = cur
            p.moveTo(*pt(*cur))
            cmd = "L"  # the pairs that follow a moveto are linetos
        elif cmd == "L":
            cur = (num(), num())
            p.lineTo(*pt(*cur))
        elif cmd == "H":
            cur = (num(), cur[1])
            p.lineTo(*pt(*cur))
        elif cmd == "V":
            cur = (cur[0], num())
            p.lineTo(*pt(*cur))
        elif cmd == "Q":
            q = (num(), num())
            e = (num(), num())
            c1 = (cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1]))
            c2 = (e[0] + 2 / 3 * (q[0] - e[0]), e[1] + 2 / 3 * (q[1] - e[1]))
            p.curveTo(*pt(*c1), *pt(*c2), *pt(*e))
            cur = e
        elif cmd == "C":
            a, b, e = (num(), num()), (num(), num()), (num(), num())
            p.curveTo(*pt(*a), *pt(*b), *pt(*e))
            cur = e
        else:  # pragma: no cover (the logo only has the commands above)
            raise ValueError(cmd)
    return p, vb[2], vb[3]


def _logo(c: canvas.Canvas, x: float, y: float, width: float, color) -> float:
    """The Sada wordmark with its lower-left corner at (x, y); returns its height."""
    try:
        p, w, h = _logo_path()
    except (OSError, AttributeError, ValueError):  # the file is missing: the page prints without the mark
        return 0.0
    k = width / w
    c.saveState()
    c.translate(x, y + h * k)
    c.scale(k, -k)
    c.setFillColor(color)
    c.drawPath(p, stroke=0, fill=1, fillMode=FILL_NON_ZERO)
    c.restoreState()
    return h * k


def _star(c: canvas.Canvas, cx: float, cy: float, r: float, color) -> None:
    """An eight-pointed star (two squares), the small ornament of the odd nights."""
    pts = []
    for k in range(16):
        a = math.pi / 8 * k
        rr = r if k % 2 == 0 else r * 0.62
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    p = c.beginPath()
    p.moveTo(*pts[0])
    for q in pts[1:]:
        p.lineTo(*q)
    p.close()
    c.setFillColor(color)
    c.drawPath(p, stroke=0, fill=1)


def _fit(text: str, font: str, size: float, room: float, floor: float = MIN_FONT) -> float:
    while size > floor and stringWidth(text, font, size) > room:
        size -= 0.5
    return size


def _put(c: canvas.Canvas, x: float, w: float, y: float, text: str, align: str, font: str, size: float) -> None:
    """Write `text` in the cell, a little smaller if it would not fit (several jumu'a times, long names)."""
    room = w - 4 * mm
    size = _fit(text, font, size, room)
    c.setFont(font, size)
    if align == "center":
        c.drawCentredString(x + w / 2, y, text)
    else:
        c.drawString(x + 2 * mm, y, text)


def _gregorian_span(first: dt.date, last: dt.date, L: dict) -> str:
    def day(d: dt.date) -> str:
        return f"{L['first'] if d.day == 1 else d.day} {L['months'][d.month - 1]}"

    a, b = day(first), f"{day(last)} {last.year}"
    if first.year != last.year:
        a += f" {first.year}"
    return f"{a} - {b}"


def groups(rows: list[dict]) -> list[list[dict]]:
    """The rows of each page: a period that is exactly one Hijri month (Ramadan) is one page, else a page per calendar month."""
    hijri = {(h.year, h.month) for h in (Gregorian.fromdate(r["date"]).to_hijri() for r in rows)}
    if len(hijri) == 1 and len(rows) <= 31:
        return [rows]
    months: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        months.setdefault((r["date"].year, r["date"].month), []).append(r)
    return list(months.values())


def _is_ramadan(month_rows: list[dict]) -> bool:
    return all(Gregorian.fromdate(r["date"]).to_hijri().month == RAMADAN for r in month_rows)


def _header(c: canvas.Canvas, mosque: dict, month_rows: list[dict], f: dict[str, str], L: dict, lang: str) -> None:
    first, last = month_rows[0]["date"], month_rows[-1]["date"]
    c.setFillColor(DEEPER)
    c.rect(0, _H - _HEADER_H, _W, _HEADER_H, stroke=0, fill=1)
    c.setFillColor(LEAF2)
    c.rect(0, _H - _HEADER_H - 1.2 * mm, _W, 1.2 * mm, stroke=0, fill=1)
    logo_w = 40 * mm
    logo_h = _logo(c, _W - _MARGIN - logo_w, _H - _HEADER_H / 2 - 7 * mm, logo_w, LEAF)
    room = _W - 2 * _MARGIN - (logo_w + 8 * mm if logo_h else 0)

    c.setFillColor(LEAF)
    c.setFont(f["sans-bold"], 8.5)
    text = c.beginText(_MARGIN, _H - 12 * mm)
    text.setFont(f["sans-bold"], 8.5)
    text.setCharSpace(0.8)
    text.textOut(L["prayer_times"])
    text.setCharSpace(0)  # the spacing belongs to the graphics state: it must not leak to the next text
    c.drawText(text)

    name = _latin(mosque.get("name") or "")[:60]
    size = _fit(name, f["serif"], 27, room, 15)
    c.setFillColor(IVORY)
    c.setFont(f["serif"], size)
    c.drawString(_MARGIN, _H - 24 * mm, name)

    if _is_ramadan(month_rows):
        h = Gregorian.fromdate(first).to_hijri()
        title, sub = f"{hijri_month(h, lang)} {h.year}", _gregorian_span(first, last, L)
    else:
        title = f"{L['months'][first.month - 1].capitalize()} {first.year}"
        if (first.year, first.month) != (last.year, last.month):  # one Hijri month over two calendar months
            title, sub = _gregorian_span(first, last, L), hijri_span(month_rows, lang)
        else:
            sub = hijri_span(month_rows, lang)
    c.setFillColor(LEAF)
    c.setFont(f["serif"], 19)
    c.drawString(_MARGIN, _H - 34 * mm, title)
    c.setFillColor(NIGHT_MUTED)
    c.setFont(f["sans"], 10.5)
    c.drawString(_MARGIN, _H - 41 * mm, sub)


def _legend(c: canvas.Canvas, y: float, f: dict[str, str], ramadan: bool, left: float, L: dict) -> None:
    x = left
    c.setFont(f["sans"], 8)
    items = [(FRIDAY, LEAF2, L["legend_friday"])]
    if ramadan:
        items.append((ODDNIGHT, GREEN, L["legend_odd_nights"]))
        items.append((DEEP, LEAF, L["legend_qadr"].format(n=27)))
    for fill, edge, text in items:
        c.setFillColor(fill)
        c.setStrokeColor(edge)
        c.setLineWidth(0.6)
        c.rect(x, y - 1 * mm, 6 * mm, 3.4 * mm, stroke=1, fill=1)
        c.setFillColor(MUTED)
        c.drawString(x + 8 * mm, y, text)
        x += 8 * mm + stringWidth(text, f["sans"], 8) + 6 * mm
    if ramadan:
        c.setFillColor(MUTED)
        c.setFont(f["sans"], 7.2)
        c.drawString(left, y - 4.6 * mm, L["legend_note"])


def _footer(c: canvas.Canvas, st: dict, f: dict[str, str], url: str, L: dict) -> None:
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(_MARGIN, _FOOTER_H, _W - _MARGIN, _FOOTER_H)
    x = _MARGIN
    if url:
        qr = QrCodeWidget(url, barLevel="M", barBorder=0, barFillColor=DEEPER)
        b = qr.getBounds()
        size = 21 * mm
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, x, 5 * mm)
        x += size + 5 * mm
        c.setFillColor(INK)
        c.setFont(f["sans-bold"], 9.5)
        c.drawString(x, 19 * mm, L["online"])
        c.setFillColor(MUTED)
        c.setFont(f["sans"], 8.5)
        c.drawString(x, 14.5 * mm, L["scan"])
        note_y = 9.5 * mm
    else:
        note_y = 15 * mm
    c.setFillColor(MUTED)
    c.setFont(f["sans"], 8.5)
    c.drawString(
        x,
        note_y,
        L["computed"] if st["source"] == "computed" else L["manual"],
    )
    # the signature, right
    c.setFillColor(GREEN)
    c.setFont(f["serif"], 13)
    c.drawRightString(_W - _MARGIN, 11 * mm, _SITE)
    logo_w = 20 * mm
    h = _logo(c, _W - _MARGIN - logo_w, 17 * mm, logo_w, DEEPER)
    if not h:
        c.setFillColor(DEEPER)
        c.setFont(f["serif"], 14)
        c.drawRightString(_W - _MARGIN, 18 * mm, "Sada")


def _page(c: canvas.Canvas, mosque: dict, st: dict, month_rows: list[dict], url: str, lang: str) -> None:
    f = _fontset()
    L = labels(lang)
    widths = [w * mm for _k, w, _a in _COLS]
    total = sum(widths)
    left = (_W - total) / 2
    ramadan_page = any(Gregorian.fromdate(r["date"]).to_hijri().month == RAMADAN for r in month_rows)  # legend
    _header(c, mosque, month_rows, f, L, lang)

    top = _H - _HEADER_H - 8 * mm
    bottom = _FOOTER_H + _LEGEND_H + 3 * mm
    starts = {i for i in range(1, len(month_rows)) if month_rows[i]["date"].month != month_rows[i - 1]["date"].month}
    row_h = min(_MAX_ROW, (top - _HEAD_ROW - bottom - len(starts) * _MONTH_ROW) / len(month_rows))
    fs = min(11.5, max(8.5, row_h / mm * 1.45))  # the text grows with the rows, within reason

    c.setFillColor(DEEP)
    c.rect(left, top - _HEAD_ROW, total, _HEAD_ROW, stroke=0, fill=1)
    c.setFillColor(LEAF)
    x = left
    for (key, _w, align), w in zip(_COLS, widths, strict=True):
        ar = PRAYER_AR.get(key)
        if ar and f["arabic"]:  # the Latin name above, the Arabic one under it (shaped, right to left)
            _put(c, x, w, top - 5.2 * mm, L["col"][key], align, f["sans-bold"], 10)
            _put(c, x, w, top - _HEAD_ROW + 2.4 * mm, arabic_visual(ar), align, f["arabic"], 10.5)
        else:
            _put(c, x, w, top - _HEAD_ROW / 2 - 1.2 * mm, L["col"][key], align, f["sans-bold"], 10)
        x += w
    y = top - _HEAD_ROW

    jumua_word = L["jumua"]
    for i, r in enumerate(month_rows):
        if i in starts:  # a dividing line: the name of the month the following days belong to
            d = r["date"]
            name = L["months"][d.month - 1].capitalize()
            c.setFillColor(DEEP)
            c.rect(left, y - _MONTH_ROW, total, _MONTH_ROW, stroke=0, fill=1)
            c.setFillColor(LEAF)
            c.setFont(f["sans-bold"], 9.5)
            c.drawString(
                left + 3 * mm,
                y - _MONTH_ROW / 2 - 1.2 * mm,
                f"{name} {d.year}" if d.year != month_rows[0]["date"].year else name,
            )
            y -= _MONTH_ROW
        h = Gregorian.fromdate(r["date"]).to_hijri()
        friday = r["date"].weekday() == 4
        night = h.month == RAMADAN and h.day in ODD_NIGHT_EVES
        qadr = night and h.day == LAYLAT_AL_QADR_EVE
        fill = DEEP if qadr else ODDNIGHT if night else FRIDAY if friday else BAND if i % 2 else colors.white
        c.setFillColor(fill)
        c.rect(left, y - row_h, total, row_h, stroke=0, fill=1)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.3)
        c.line(left, y - row_h, left + total, y - row_h)
        if friday:
            c.setFillColor(LEAF2)
            c.rect(left, y - row_h, 1.4 * mm, row_h, stroke=0, fill=1)
        base = y - row_h / 2 - fs * 0.17
        dhuhr = " / ".join(r["jumua"]) if friday and r["jumua"] else r["times"]["dhuhr"]
        cells = [
            f"{L['weekdays'][r['date'].weekday()]} {r['date'].day}",
            f"{h.day} {hijri_month(h, lang)} {h.year}",
            r["times"]["fajr"],
            r["times"]["sunrise"],
            dhuhr,
            r["times"]["asr"],
            r["times"]["maghrib"],
            r["times"]["isha"],
        ]
        ink, muted = (IVORY, NIGHT_MUTED) if qadr else (INK, MUTED)
        font = f["sans-bold"] if (friday or night) else f["sans"]
        x = left
        for (_key, _w, align), w, text in zip(_COLS, widths, cells, strict=True):
            c.setFillColor(ink if text else muted)
            _put(c, x, w, base, text or "-", align, font, fs if align == "center" else fs - 0.5)
            x += w
        # the tags: jumu'a on a Friday, the night's number on the odd nights
        tag_size = max(6.5, min(8, fs - 3))
        if friday:
            c.setFillColor(LEAF if qadr else GREEN)
            c.setFont(f["sans-bold"], tag_size)
            c.drawRightString(left + widths[0] - 2 * mm, base, jumua_word)
        if night:
            tag = L["laylat_al_qadr"] if qadr else L["night"].format(n=h.day + 1)
            tag_size = _fit(tag, f["sans-bold"], tag_size, 22 * mm, 5.5)
            c.setFont(f["sans-bold"], tag_size)
            tx = left + widths[0] + widths[1] - 2 * mm
            c.setFillColor(LEAF if qadr else GREEN)
            c.drawRightString(tx, base, tag)
            _star(
                c,
                tx - stringWidth(tag, f["sans-bold"], tag_size) - 2.6 * mm,
                base + tag_size * 0.3,
                1.5 * mm,
                LEAF if qadr else LEAF2,
            )
        y -= row_h
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(left, y, total, top - y, stroke=1, fill=0)
    _legend(c, _FOOTER_H + 9 * mm, f, ramadan_page, left, L)
    _footer(c, st, f, url, L)


def public_page_url(base: str, slug: str) -> str:
    """The public page of a mosque's timetable, the target of the QR code."""
    return f"{base.rstrip('/')}/api/v1/public/mosques/{quote(slug)}/prayer-times/page"


def pdf_bytes(conn, mosque: dict, first: dt.date, last: dt.date, base_url: str = "", lang: str = DEFAULT_LANG) -> bytes:
    """The calendar of the period: a page per calendar month, one page for a whole Hijri month (Ramadan).

    `lang` is the language of the printed wording (a key of `prayer_labels.LABELS`, French by default):
    the mosque's, not the reader's.

    `base_url` is the address of this Sada: with it, and when the mosque publishes its times, the
    footer carries a QR code to the public page.
    """
    rows = days(conn, mosque, first, last)
    st = pt.read(conn, mosque)
    url = public_page_url(base_url, mosque["slug"]) if base_url and st.get("public") else ""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, invariant=1)
    c.setTitle(_latin(f"{mosque.get('name') or ''} - {first.isoformat()} / {last.isoformat()}"))
    c.setCreator("Sada")
    c.setAuthor("Sada")
    for month_rows in groups(rows):
        _page(c, mosque, st, month_rows, url, lang)
        c.showPage()
    c.save()
    return buf.getvalue()
