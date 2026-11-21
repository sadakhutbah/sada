"""The public prayer-time screen of a mosque, in the manner of a mosque display: a large live clock, the
Gregorian and Hijri dates, the prayers with their adhan (and their iqama when the mosque publishes one),
the next prayer with its countdown. Meant for a TV in landscape as much as for a phone in portrait.

French, with the Arabic names of the prayers. Nothing is invented: a time, an iqama or a Jumu'a the
mosque does not publish is simply not shown. The page is complete without script (server-rendered); the
script (`static/prayer-screen.js`, same origin, no third party) only keeps the clock, the countdown and
the day up to date, fetching the next days from the `days` endpoint at midnight.
"""

from __future__ import annotations

import datetime as dt
import json
from html import escape
from urllib.parse import quote

from hijridate import Gregorian

from . import prayer_labels
from . import prayer_times as pt

#: Prayers shown, in order, with their French and Arabic names. `sunrise` is a time, not a prayer.
ROWS = (
    ("fajr", "Fajr", "الفجر"),
    ("sunrise", "Chourouk", "الشروق"),
    ("dhuhr", "Dhuhr", "الظهر"),
    ("asr", "Asr", "العصر"),
    ("maghrib", "Maghrib", "المغرب"),
    ("isha", "Isha", "العشاء"),
)
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = prayer_labels.labels("fr")["months"]
HIJRI_MONTHS = prayer_labels.labels("fr")["hijri_months"]
#: Days sent to the browser: today and the next one (the Fajr after Isha is tomorrow's).
SPAN = 2

ASSETS = "/api/v1/public/assets"  # not /static: on the public host only /api/v1/public/* reaches the back office
CSS = f"{ASSETS}/prayer-screen.css"
JS = f"{ASSETS}/prayer-screen.js"
FONTS = f"{ASSETS}/fonts.css"


def greg_text(day: dt.date) -> str:
    return f"{WEEKDAYS[day.weekday()].capitalize()} {day.day} {MONTHS[day.month - 1]} {day.year}"


def hijri_text(day: dt.date) -> str:
    h = Gregorian.fromdate(day).to_hijri()
    return f"{h.day} {HIJRI_MONTHS[h.month - 1]} {h.year}"


def day_card(conn, mosque: dict, st: dict, day: dt.date) -> dict:
    """One day as the screen shows it: dates, adhan times, iqama times, jumu'a (Friday). Times that are
    not published are absent; a day with nothing has an empty `times`."""
    try:
        f = pt.day_fields(conn, mosque, st, day)
    except pt.calc.InvalidParams:
        f = {}
    return {
        "date": day.isoformat(),
        "weekday": WEEKDAYS[day.weekday()],
        "gregorian": greg_text(day),
        "hijri": hijri_text(day),
        "times": {k: f[k] for k in pt.FIELDS if f.get(k)},
        "iqama": {p: f[f"{p}_iqama"] for p in pt.PRAYERS if f.get(f"{p}_iqama")},
        "jumua": list(st["jumua"]) if day.weekday() == 4 else [],
    }


def days(conn, mosque: dict, first: dt.date, n: int = SPAN) -> dict:
    st = pt.read(conn, mosque)
    return {
        "mosque": mosque.get("name") or "",
        "timezone": pt._tz_key(mosque),
        "days": [day_card(conn, mosque, st, first + dt.timedelta(days=i)) for i in range(n)],
    }


def _json_in_html(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _rows(today: dict) -> str:
    out = []
    for key, fr, ar in ROWS:
        t = today["times"].get(key, "")
        if key == "sunrise":  # sunrise is only a time: no adhan label, no iqama
            out.append(
                f'<li class="row sun" data-p="{key}"{"" if t else " hidden"}>'
                f'<span class="nm"><span class="fr">{fr}</span><span class="ar" lang="ar" dir="rtl">{ar}</span></span>'
                f'<span class="ad"><time>{escape(t)}</time></span></li>'
            )
            continue
        q = today["iqama"].get(key, "")
        out.append(
            f'<li class="row prayer" data-p="{key}"{"" if t else " hidden"}>'
            f'<span class="nm"><span class="fr">{fr}</span><span class="ar" lang="ar" dir="rtl">{ar}</span></span>'
            f'<span class="ad"><span class="lbl">Adhan</span><time>{escape(t)}</time></span>'
            f'<span class="iq"{"" if q else " hidden"}><span class="lbl">Iqama</span><time>{escape(q)}</time></span>'
            "</li>"
        )
    jum = " · ".join(today["jumua"])
    out.append(
        f'<li class="row jumua" data-p="jumua"{"" if jum else " hidden"}>'
        '<span class="nm"><span class="fr">Jumu\'a</span><span class="ar" lang="ar" dir="rtl">الجمعة</span></span>'
        f'<span class="ad"><span class="lbl">Prière du vendredi</span><time>{escape(jum)}</time></span></li>'
    )
    return "".join(out)


def refused(title: str, body: str = "") -> str:
    return _doc("Sada", f'<main class="plain"><h1>{escape(title)}</h1>{body}</main>', data="")


def _doc(title: str, body: str, data: str) -> str:
    return (
        '<!doctype html><html lang="fr" dir="ltr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="color-scheme" content="dark"><link rel="icon" href="data:,">'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="{FONTS}">'
        f'<link rel="stylesheet" href="{CSS}"></head><body>{body}{data}</body></html>'
    )


def render(slug: str, mosque: dict, payload: dict, month: str) -> str:
    """The screen, server-rendered with today; `payload` is `days(...)`."""
    today = payload["days"][0]
    base = f"/api/v1/public/mosques/{quote(slug)}/prayer-times"
    name = escape(mosque.get("name") or "")
    status = "" if today["times"] else '<p class="none" role="status">Aucun horaire saisi pour ce jour.</p>'
    iqama_note = (
        "" if today["iqama"] or not today["times"] else '<p class="note">Cette mosquée ne publie pas d\'iqama.</p>'
    )
    body = f"""<div class="screen" id="screen" data-base="{base}">
<header class="top"><h1 class="mosque">{name}</h1>
<button type="button" class="fs" id="fs" hidden>Plein écran</button></header>
<section class="clockbox" aria-label="Heure et date">
<time class="clock" id="clock" aria-hidden="true">--:--:--</time>
<p class="dates"><span id="greg">{escape(today["gregorian"])}</span><span class="sep" aria-hidden="true"> · </span><span id="hijri">{escape(today["hijri"])} H</span></p>
</section>
<section class="next" id="next" aria-label="Prochaine prière">
<p class="kicker" id="next-kicker">Prochaine prière</p>
<p class="nextname"><span id="next-fr">—</span> <span class="ar" id="next-ar" lang="ar" dir="rtl"></span></p>
<p class="count" id="count" aria-hidden="true">--:--:--</p>
<p class="sr" id="next-sr" role="status" aria-live="polite"></p>
</section>
<section class="list" aria-label="Horaires du jour">
{status}<div class="cols{"" if today["iqama"] else " noiqama"}" id="cols" aria-hidden="true"><span></span><span>Adhan</span><span{"" if today["iqama"] else " hidden"}>Iqama</span></div>
<ul class="rows{"" if today["iqama"] else " noiqama"}" id="rows">{_rows(today)}</ul>{iqama_note}
</section>
<footer class="foot"><a href="{base}/page?month={escape(month)}">Calendrier du mois</a>
<a href="{base}/export?format=pdf&amp;month={escape(month)}" download>Calendrier PDF</a></footer>
</div>"""
    data = (
        f'<script type="application/json" id="ps-data">{_json_in_html(payload)}</script>'
        f'<script src="{JS}" defer></script>'
    )
    return _doc(f"{mosque.get('name') or ''} — Horaires de prière", body, data)
