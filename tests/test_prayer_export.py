"""Printable calendar (PDF) and CSV of a mosque's prayer times, in the console and on the public page."""

import datetime as dt
import io

import pytest

pytest.importorskip("fastapi")
pypdf = pytest.importorskip("pypdf")

from conftest import client, one, set_mosque  # noqa: E402

from sada.backoffice import models, prayer_export, prayer_times  # noqa: E402
from sada.backoffice.api import public  # noqa: E402

CALC = {"lat": "48.9", "lon": "2.3", "method": "uoif"}
CONSOLE = "/api/v1/settings/prayer/export"
PUBLIC = "/api/v1/public/mosques/ma-mosquee/prayer-times"


@pytest.fixture(autouse=True)
def _fresh_limits():
    public.reset()


def _choose(env, source="computed", **extra):
    c = client(env, "admin")
    body = {"source": source, "calc": CALC, "jumua": ["13:00", "14:15"], **extra}
    assert c.ecrire("/settings/prayer", body, methode="PUT").status_code == 200
    return c


def _pages(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF")
    return [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(resp.content)).pages]


# ------------------------------------------------------------------ the period


def test_period_month_range_year_and_ramadan():
    today = dt.date(2027, 1, 10)
    assert prayer_export.period(today, month="2027-02") == (dt.date(2027, 2, 1), dt.date(2027, 2, 28))
    assert prayer_export.period(today, start="2027-03-05", end="2027-03-09") == (
        dt.date(2027, 3, 5),
        dt.date(2027, 3, 9),
    )
    assert prayer_export.period(today, start="2027-03-05") == (dt.date(2027, 3, 5), dt.date(2027, 3, 5))
    assert prayer_export.period(today) == (dt.date(2027, 1, 1), dt.date(2027, 12, 31))
    # Ramadan 1448 (Umm al-Qura): the next one before it, the same one while it lasts, the following year after it
    assert prayer_export.period(today, ramadan_=True) == (dt.date(2027, 2, 8), dt.date(2027, 3, 8))
    assert prayer_export.period(dt.date(2027, 3, 1), ramadan_=True) == (dt.date(2027, 2, 8), dt.date(2027, 3, 8))
    assert prayer_export.period(dt.date(2027, 4, 1), ramadan_=True)[0].year == 2028


@pytest.mark.parametrize(
    "kw",
    [
        {"month": "2027-13"},
        {"start": "x"},
        {"start": "2027-03-09", "end": "2027-03-05"},
        {"start": "2027-01-01", "end": "2028-01-02"},  # a year and two days
        {"year": 1999},
    ],
)
def test_bad_periods_are_refused(kw):
    with pytest.raises(ValueError):
        prayer_export.period(dt.date(2027, 1, 10), **kw)


def test_file_names():
    f = prayer_export.file_name
    assert f(dt.date(2027, 3, 1), dt.date(2027, 3, 31), "pdf") == "prayer-times-2027-03.pdf"
    assert f(dt.date(2027, 1, 1), dt.date(2027, 12, 31), "csv") == "prayer-times-2027.csv"
    assert f(dt.date(2027, 2, 8), dt.date(2027, 3, 9), "pdf") == "prayer-times-2027-02-08_2027-03-09.pdf"


# ------------------------------------------------------------------ PDF


def test_a_month_is_one_a4_portrait_page_with_the_mosque_the_days_and_the_jumua(env):
    c = _choose(env)
    r = c.get(CONSOLE + "?format=pdf&month=2027-03")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="prayer-times-2027-03.pdf"'
    reader = pypdf.PdfReader(io.BytesIO(r.content))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (595, 842)  # A4 portrait
    text = _pages(r)[0]
    assert "Mosquée Exemple" in text and "March 2027" in text and "Ramadan - Shawwal 1448" in text
    assert text.count("Fri") == 4 and text.count("Jumu'a") == 4  # the four Fridays of March 2027, highlighted
    assert text.count("13:00 / 14:15") == 4  # the jumu'a times stand in the Dhuhr column
    for day in ("Mon 1", "Wed 31"):
        assert day in text
    times = prayer_times.computed_day(
        prayer_times.read(env["conn"], _mosque(env))["calc"], _mosque(env), dt.date(2027, 3, 1)
    )
    for k in ("fajr", "sunrise", "asr", "maghrib", "isha"):
        assert times[k] in text


def _mosque(env):
    return one(env["conn"], models.Mosque, id=env["mid"])


def test_a_period_gets_one_page_per_calendar_month_and_ramadan_is_two(env):
    c = _choose(env)
    pages = _pages(c.get(CONSOLE + "?format=pdf&start=2027-02-08&end=2027-03-08"))
    assert len(pages) == 2
    assert "February 2027" in pages[0] and "March 2027" in pages[1]
    assert "Ramadan 1448" in pages[0] and "Ramadan" in pages[1]
    assert "1 Ramadan" in pages[0] and "29 Ramadan" in pages[1]
    assert len(_pages(c.get(CONSOLE + "?format=pdf&year=2027"))) == 12


def test_ramadan_asks_for_the_hijri_period_itself(env):
    c = _choose(env)
    r = c.get(CONSOLE + "?format=pdf&ramadan=true")
    first, last = prayer_export.ramadan(prayer_times.local_today(_mosque(env)))
    assert r.headers["content-disposition"].endswith(f'{first.isoformat()}_{last.isoformat()}.pdf"')
    assert len(_pages(r)) in (1, 2)


def test_the_pdf_is_in_the_readers_language_and_survives_an_arabic_name(env):
    c = _choose(env)
    set_mosque(env["conn"], env["mid"], name="مسجد النور – Café")
    fr = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03", headers={"X-Sada-Lang": "fr"}))[0]
    assert "Mars 2027" in fr and "Ven" in fr and "Chourouk" in fr and "Hégire" in fr and "Café" in fr
    ar = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03", headers={"X-Sada-Lang": "ar"}))[0]
    assert "March 2027" in ar  # Helvetica cannot draw Arabic: the English label stays, nothing breaks


def test_a_manual_timetable_prints_the_days_it_has_and_a_dash_for_a_missing_time(env):
    c = _choose(env, "manual")
    c.ecrire(
        "/settings/prayer/days",
        {"month": "2027-03", "days": [{"date": "2027-03-05", "fajr": "05:11", "dhuhr": "13:01"}]},
        methode="PUT",
    )
    text = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03"))[0]
    assert "05:11" in text and "13:00 / 14:15" in text and "Fri 5" in text and "-" in text
    assert "Mon 1" not in text  # nothing typed for that day: no line
    empty = c.get(CONSOLE + "?format=pdf&month=2027-04")
    assert empty.status_code == 422 and "No prayer time" in empty.json()["message"]


def test_bad_requests_are_readable(env):
    c = _choose(env)
    for q in ("format=ics", "format=pdf&month=2027-13", "format=pdf&start=2027-03-09&end=2027-03-05"):
        r = c.get(f"{CONSOLE}?{q}")
        assert r.status_code == 422 and r.json()["message"], q


# ------------------------------------------------------------------ CSV


def test_the_csv_of_a_period_is_taken_back_by_the_import(env):
    c = _choose(env)
    r = c.get(CONSOLE + "?format=csv&start=2027-03-05&end=2027-03-07")
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].endswith('2027-03-05_2027-03-07.csv"')
    lines = r.text.splitlines()
    assert lines[0].startswith("date,fajr,sunrise,dhuhr,asr,maghrib,isha") and len(lines) == 4
    c.ecrire("/settings/prayer", {"source": "manual", "calc": CALC, "jumua": []}, methode="PUT")
    assert c.ecrire("/settings/prayer/import", {"csv": r.text}).json()["imported"] == 3
    again = c.get(CONSOLE + "?format=csv&start=2027-03-05&end=2027-03-07")
    assert again.text == r.text


def test_the_old_year_export_is_unchanged(env):
    c = _choose(env)
    out = c.get(CONSOLE + "?year=2027")
    assert out.headers["content-disposition"].endswith('prayer-times-2027.csv"') and out.text.count("\n") == 366


# ------------------------------------------------------------------ Mawaqit, rights


def test_a_mawaqit_mosque_has_nothing_to_export_anywhere(env):
    c = client(env, "admin")
    c.ecrire("/settings/prayer", {"source": "mawaqit", "mawaqit_url": "some-mosque-city"}, methode="PUT")
    for fmt in ("pdf", "csv"):
        assert c.get(f"{CONSOLE}?format={fmt}&month=2027-03").status_code == 422
        assert client(env).get(f"{PUBLIC}/export?format={fmt}&month=2027-03").status_code == 404
    page = client(env).get(f"{PUBLIC}/page")
    assert page.status_code == 404 and "https://mawaqit.net/fr/some-mosque-city" in page.text
    assert "Fajr" not in page.text


def test_only_an_admin_exports_from_the_console(env):
    _choose(env)
    assert client(env).get(CONSOLE + "?format=pdf&month=2027-03").status_code in (401, 403)


# ------------------------------------------------------------------ public page


def test_the_public_export_needs_no_login_and_follows_the_public_switch(env):
    a = _choose(env)
    r = client(env).get(PUBLIC + "/export?format=pdf&month=2027-03")  # no session at all
    assert len(_pages(r)) == 1
    assert r.headers["cache-control"] == "public, max-age=60"
    csv = client(env).get(PUBLIC + "/export?format=csv&month=2027-03")
    assert csv.status_code == 200 and csv.text.count("\n") == 32
    default = client(env).get(PUBLIC + "/export")  # a PDF of the current month
    assert default.status_code == 200 and default.headers["content-type"] == "application/pdf"
    a.ecrire("/settings/prayer", {"source": "computed", "calc": CALC, "public": "0"}, methode="PUT")
    assert client(env).get(PUBLIC + "/export?format=pdf&month=2027-03").status_code == 404
    assert client(env).get(PUBLIC + "/page").status_code == 404
    assert "ma-mosquee" not in client(env).get(PUBLIC + "/export?format=pdf").text


def test_the_public_export_is_bounded_and_limited(env):
    _choose(env)
    c = client(env)
    assert c.get(PUBLIC + "/export?format=pdf&start=2027-01-01&end=2028-06-01").status_code == 422
    assert c.get(PUBLIC + "/export?format=ics").status_code == 422
    public.reset()
    codes = {c.get(PUBLIC + "/export?format=csv&month=2027-03").status_code for _ in range(public.RATE_PER_MINUTE + 3)}
    assert codes == {200, 429}


def test_the_public_page_shows_the_month_with_its_downloads_and_escapes_everything(env):
    _choose(env)
    set_mosque(env["conn"], env["mid"], name='<script>alert("x")</script> & Cie')
    r = client(env).get(PUBLIC + "/page?month=2027-03")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text
    assert r.text.count('class="fri"') == 4 and "13:00 / 14:15" in r.text
    for href in (
        f"{PUBLIC}/export?format=pdf&amp;month=2027-03",
        f"{PUBLIC}/export?format=csv&amp;month=2027-03",
        f"{PUBLIC}/export?format=pdf&amp;ramadan=1",
        f"{PUBLIC}/page?month=2027-02",
        f"{PUBLIC}/page?month=2027-04",
    ):
        assert f'href="{href}"' in r.text, href
    assert "token" not in r.text.lower() and "mawaqit" not in r.text.lower()
    assert client(env).get(PUBLIC + "/page?month=2027-13").status_code == 422
    fr = client(env).get(PUBLIC + "/page?month=2027-03", headers={"X-Sada-Lang": "fr"})
    assert "Télécharger le PDF" in fr.text and 'lang="fr"' in fr.text
    ar = client(env).get(PUBLIC + "/page?month=2027-03", headers={"X-Sada-Lang": "ar"})
    assert 'dir="rtl"' in ar.text
