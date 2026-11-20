"""The public, read-only page of a mosque's own timetable: one month, and the PDF / CSV downloads.

Plain HTML with a little inline style (no script, nothing to fetch): it prints well, works on a phone,
and carries only what the public endpoint already publishes. Everything written into it is escaped.
"""

from __future__ import annotations

import datetime as dt
from html import escape
from urllib.parse import quote

from .. import i18n
from ..i18n import _
from . import prayer_export as export

_STYLE = """
body{margin:0;font:16px/1.4 system-ui,sans-serif;background:#f4f1e8;color:#1f2a24}
header{background:#2f6b4f;color:#fff;padding:18px 16px;text-align:center}
h1{margin:0;font-size:1.5rem}header p{margin:4px 0 0;opacity:.9}
main{max-width:760px;margin:0 auto;padding:14px 10px 30px}
nav,.dl{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;align-items:center;margin:10px 0}
a.btn{display:inline-block;padding:9px 14px;border-radius:8px;border:1px solid #2f6b4f;color:#2f6b4f;background:#fff;
text-decoration:none;font-weight:600}
a.btn.main{background:#2f6b4f;color:#fff}
table{width:100%;border-collapse:collapse;background:#fff;font-variant-numeric:tabular-nums}
th{background:#1f2a24;color:#fff;padding:7px 4px;font-size:.85rem}
td{padding:6px 4px;text-align:center;border-bottom:1px solid #e3dcc7;font-size:.95rem}
td.d,th.d{text-align:left;padding-left:8px}td.h{text-align:left;color:#6b6f66;font-size:.85rem}
tr.fri td{background:#f3e3b3;font-weight:700}
.wrap{overflow-x:auto}
.empty{text-align:center;color:#6b6f66;padding:24px}
@media print{nav,.dl{display:none}header{background:none;color:#000}body{background:#fff}}
@media(max-width:560px){td.h,th.h{display:none}td,th{font-size:.82rem;padding:5px 2px}}
"""


def _shift(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _doc(title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="{i18n.current()}" dir="{"rtl" if i18n.current() == "ar" else "ltr"}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def refused(body: dict, status: int) -> str:
    """A short page for a mosque that does not publish its times here (a Mawaqit mosque links to its page)."""
    msg = escape(body.get("message") or body.get("error") or "")
    link = ""
    url = body.get("mawaqit_url") or ""
    if status == 404 and url.startswith("https://"):
        link = f'<p class="dl"><a class="btn main" href="{escape(url, quote=True)}" rel="noopener">Mawaqit</a></p>'
    return _doc(msg, f"<header><h1>{msg}</h1></header><main>{link}</main>")


def render(slug: str, mosque: dict, month: str, rows: list[dict], st: dict) -> str:
    base = f"/api/v1/public/mosques/{quote(slug)}/prayer-times"
    first = dt.date.fromisoformat(month + "-01")
    title = f"{export.label(export.MONTHS[first.month - 1])} {first.year}"
    name = escape(mosque.get("name") or "")
    prev_m, next_m = _shift(month, -1), _shift(month, 1)
    head = "".join(
        f'<th class="{c}">{escape(export.label(k))}</th>'
        for k, c in (
            ("Date", "d"), ("Hijri", "h"), ("Fajr", ""), ("Sunrise", ""), ("Dhuhr", ""), ("Asr", ""),
            ("Maghrib", ""), ("Isha", ""),
        )
    )  # fmt: skip
    jumua_word = escape(export.label("Jumu'a"))
    lines = []
    for r in rows:
        d = r["date"]
        fri = d.weekday() == 4
        t = r["times"]
        dhuhr = " / ".join(r["jumua"]) if fri and r["jumua"] else t["dhuhr"]
        wd = escape(export.label(export.WEEKDAYS[d.weekday()]))
        hij = escape(r["hijri"])
        cells = "".join(
            f"<td>{escape(v or '-')}</td>" for v in (t["fajr"], t["sunrise"], dhuhr, t["asr"], t["maghrib"], t["isha"])
        )
        lines.append(
            f'<tr class="{"fri" if fri else ""}"><td class="d">{wd} {d.day}{" · " + jumua_word if fri else ""}</td>'
            f'<td class="h">{hij}</td>{cells}</tr>'
        )
    table = (
        f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(lines)}</tbody></table></div>'
        if rows
        else f'<p class="empty">{escape(_("No prayer time entered for this period."))}</p>'
    )
    q = f"month={month}"
    buttons = (
        f'<p class="dl"><a class="btn main" href="{base}/export?format=pdf&amp;{q}" download>{escape(_("Download the PDF"))}</a>'
        f'<a class="btn" href="{base}/export?format=csv&amp;{q}" download>{escape(_("Download the CSV"))}</a>'
        f'<a class="btn" href="{base}/export?format=pdf&amp;ramadan=1" download>{escape(_("Ramadan calendar (PDF)"))}</a></p>'
    )
    nav = (
        f'<nav><a class="btn" href="{base}/page?month={prev_m}" rel="prev">&larr;</a>'
        f"<strong>{escape(title)}</strong>"
        f'<a class="btn" href="{base}/page?month={next_m}" rel="next">&rarr;</a></nav>'
    )
    sub = escape(export.hijri_span(rows)) if rows else ""
    body = f"<header><h1>{name}</h1><p>{sub}</p></header><main>{nav}{buttons}{table}</main>"
    return _doc(f"{mosque.get('name') or ''} - {title}", body)
