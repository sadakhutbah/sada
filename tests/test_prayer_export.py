"""Printable calendar (PDF) and CSV of a mosque's prayer times, in the console and on the public page."""

import datetime as dt
import io
import re

import arabic_reshaper
import pytest

pytest.importorskip("fastapi")
pypdf = pytest.importorskip("pypdf")

from conftest import client, one, set_mosque  # noqa: E402

from sada.backoffice import models, prayer_export, prayer_labels, prayer_times  # noqa: E402
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
    assert "Mosquée Exemple" in text and "Mars 2027" in text and "Ramadan - Chawwal 1448" in text
    assert "Horaires de prière" in text  # French by default, whatever the reader's language
    assert (
        len(re.findall(r"Ven\. \d+", text)) == 4 and text.count("Jumu'a") == 5
    )  # the four Fridays of March 2027 + the legend
    assert text.count("13:00 / 14:15") == 4  # the jumu'a times stand in the Dhuhr column
    assert "sadakhutbah.org" in text and "Horaires en ligne" in text  # the Sada footer and the QR code's caption
    assert text.count("Ramadan 1448") == 8  # titled March, not Ramadan: its first eight days are the last ones of it
    assert "Nuit du Destin" in text and "Nuits impaires" in text
    for day in ("Lun. 1", "Mer. 31"):
        assert day in text
    assert "1 Chawwal 1448" in text
    for col in ("Fajr", "Chourouk", "Dhuhr", "Asr", "Maghrib", "Isha"):
        assert col in text
    times = prayer_times.computed_day(
        prayer_times.read(env["conn"], _mosque(env))["calc"], _mosque(env), dt.date(2027, 3, 1)
    )
    for k in ("fajr", "sunrise", "asr", "maghrib", "isha"):
        assert times[k] in text


def _mosque(env):
    return one(env["conn"], models.Mosque, id=env["mid"])


def test_a_period_gets_one_page_per_calendar_month(env):
    c = _choose(env)
    pages = _pages(c.get(CONSOLE + "?format=pdf&start=2027-02-01&end=2027-03-31"))
    assert len(pages) == 2
    assert "Février 2027" in pages[0] and "Mars 2027" in pages[1]
    assert len(_pages(c.get(CONSOLE + "?format=pdf&year=2027"))) == 12


def test_ramadan_is_one_page_titled_ramadan_1448_with_its_odd_nights(env):
    c = _choose(env)
    pages = _pages(c.get(CONSOLE + "?format=pdf&start=2027-02-08&end=2027-03-08"))
    assert len(pages) == 1  # a whole Hijri month fills one page
    text = pages[0]
    assert "Ramadan 1448" in text and "8 février - 8 mars 2027" in text
    assert "1 Ramadan 1448" in text and "29 Ramadan 1448" in text
    assert (
        text.count("Nuit ") == 6 and text.count("Nuit du Destin") == 2
    )  # nights 21-29, the 27th being "du Destin", + legend
    assert "Nuit 21" in text and "Nuit 29" in text and "Nuits impaires" in text
    assert "Ven. 12" in text and len(re.findall(r"Ven\. \d+", text)) == 4 and text.count("Jumu'a") == 5  # + legend
    assert "Horaires de prière" in text


def test_the_footer_has_a_qr_code_only_for_a_published_timetable(env):
    c = _choose(env)
    shown = c.get(CONSOLE + "?format=pdf&month=2027-03").content
    c.ecrire("/settings/prayer", {"source": "computed", "calc": CALC, "public": "0"}, methode="PUT")
    hidden = c.get(CONSOLE + "?format=pdf&month=2027-03").content
    assert len(shown) > len(hidden)  # the QR code is drawn in the first one only
    assert "Horaires en ligne" not in _pages(c.get(CONSOLE + "?format=pdf&month=2027-03"))[0]
    url = prayer_export.public_page_url("https://sada.example/", "ma-mosquee")
    assert url == "https://sada.example/api/v1/public/mosques/ma-mosquee/prayer-times/page"


def test_the_wordmark_is_the_one_of_the_charter():
    path, width, height = prayer_export._logo_path()
    assert (width, height) == (1000, 383)
    assert len(path.getCode()) > 1000  # the whole outline, not an empty path


def test_ramadan_asks_for_the_hijri_period_itself(env):
    c = _choose(env)
    r = c.get(CONSOLE + "?format=pdf&ramadan=true")
    first, last = prayer_export.ramadan(prayer_times.local_today(_mosque(env)))
    assert r.headers["content-disposition"].endswith(f'{first.isoformat()}_{last.isoformat()}.pdf"')
    assert len(_pages(r)) in (1, 2)


def test_the_pdf_is_in_the_mosques_language_not_the_readers_and_survives_an_arabic_name(env):
    c = _choose(env)
    set_mosque(env["conn"], env["mid"], name="مسجد النور – Café")
    for lang in ("fr", "en", "ar"):  # the reader's language does not change the printed wording
        text = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03", headers={"X-Sada-Lang": lang}))[0]
        assert "Mars 2027" in text and "Ven. 5" in text and "Chourouk" in text and "Hégire" in text and "Café" in text


def test_the_wording_comes_from_a_table_per_language_french_by_default(env):
    assert prayer_labels.DEFAULT_LANG == "fr"
    assert prayer_labels.labels() is prayer_labels.labels("fr") is prayer_labels.labels("xx")
    assert prayer_labels.labels("fr-FR")["prayer_times"] == "Horaires de prière"
    assert set(prayer_labels.LABELS["en"]) == set(prayer_labels.LABELS["fr"])  # a language fills every key
    mosque = _mosque(env)
    assert mosque
    prayer_times.save(env["conn"], mosque, source="computed", public=True, jumua=["13:00"], cfg=CALC)
    first, last = dt.date(2027, 3, 1), dt.date(2027, 3, 31)
    en = pypdf.PdfReader(io.BytesIO(prayer_export.pdf_bytes(env["conn"], mosque, first, last, lang="en")))
    text = en.pages[0].extract_text()
    assert "March 2027" in text and "Sunrise" in text and "Fri 5" in text and "Prayer times" in text
    assert prayer_export.hijri_text(dt.date(2027, 2, 8), "fr") == "1 Ramadan 1448"
    assert prayer_export.hijri_text(dt.date(2027, 3, 9), "fr") == "1 Chawwal 1448"
    assert prayer_export.hijri_text(dt.date(2027, 3, 9)) == "1 Shawwal 1448"  # the public page keeps its own


def test_the_arabic_names_are_shaped_and_set_right_to_left(env):
    c = _choose(env)
    text = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03"))[0]
    for word in ("الفجر", "الشروق", "الظهر", "العصر", "المغرب", "العشاء"):
        joined = arabic_reshaper.reshape(word)
        assert joined != word and joined in text, word  # joined letters (presentation forms), read back right to left
        # drawn left to right, so the glyphs are laid out in reverse: the first letter ends on the right
        assert prayer_export.arabic_visual(word) == joined[::-1]
    assert "?" not in text.replace("-", "")


def test_a_manual_timetable_prints_the_days_it_has_and_a_dash_for_a_missing_time(env):
    c = _choose(env, "manual")
    c.ecrire(
        "/settings/prayer/days",
        {"month": "2027-03", "days": [{"date": "2027-03-05", "fajr": "05:11", "dhuhr": "13:01"}]},
        methode="PUT",
    )
    text = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03"))[0]
    assert "05:11" in text and "13:00 / 14:15" in text and "Ven. 5" in text and "-" in text
    assert "Lun. 1" not in text  # nothing typed for that day: no line
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


def test_a_page_spanning_two_months_has_a_dividing_line_naming_the_new_one(env):
    c = _choose(env)
    ramadan = _pages(c.get(CONSOLE + "?format=pdf&start=2027-02-08&end=2027-03-08"))[0]
    assert ramadan.count("Mars") == 1  # the capitalised band, the title says "mars" in lower case
    assert ramadan.index("Dim. 28") < ramadan.index("Mars") < ramadan.index("Lun. 1\n")
    assert len(re.findall(r"\b(?:Lun|Mar|Mer|Jeu|Ven|Sam|Dim)\. \d+", ramadan)) == 29  # every day still there
    # a page of a single month needs none
    march = _pages(c.get(CONSOLE + "?format=pdf&month=2027-03"))[0]
    assert march.count("Mars") == 1 and "Mars 2027" in march
