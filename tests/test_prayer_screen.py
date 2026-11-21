"""The public prayer-time screen: clock page, days endpoint and the few assets served under the public prefix."""

import datetime as dt
import json
import re

import pytest

pytest.importorskip("fastapi")

from conftest import client, set_mosque  # noqa: E402

from sada.backoffice import prayer_screen  # noqa: E402
from sada.backoffice.api import public  # noqa: E402

CALC = {"lat": "48.9", "lon": "2.3", "method": "uoif"}
BASE = "/api/v1/public/mosques/ma-mosquee/prayer-times"


@pytest.fixture(autouse=True)
def _fresh_limits():
    public.reset()


def _choose(env, source="computed", **extra):
    c = client(env, "admin")
    body = {"source": source, "calc": CALC, "jumua": ["13:00", "14:15"], **extra}
    assert c.ecrire("/settings/prayer", body, methode="PUT").status_code == 200
    return c


def _data(html: str) -> dict:
    return json.loads(re.search(r'id="ps-data">(.*?)</script>', html, re.S).group(1))


def test_dates_are_in_french_with_the_hijri_date():
    assert prayer_screen.greg_text(dt.date(2027, 3, 9)) == "Mardi 9 mars 2027"
    assert prayer_screen.hijri_text(dt.date(2027, 3, 9)) == "1 Chawwal 1448"


def test_the_screen_shows_the_prayers_in_french_and_arabic_with_the_iqama_that_exists(env):
    _choose(env, calc={**CALC, "iqama": {"isha": 10}})
    r = client(env).get(BASE + "/page")  # no session at all
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"].startswith("public, max-age=60")
    h = r.text
    for word in ("Fajr", "Chourouk", "Dhuhr", "Asr", "Maghrib", "Isha", "الفجر", "العشاء", "Mosquée Exemple"):
        assert word in h, word
    day = _data(h)["days"][0]
    assert set(day["iqama"]) == {"isha"} and day["times"]["isha"] and day["date"] == dt.date.today().isoformat()
    assert 'lang="fr"' in h and 'role="status"' in h and "aria-label" in h


def test_chourouk_is_only_a_time_without_adhan_label_nor_iqama(env):
    _choose(env, calc={**CALC, "iqama": {"isha": 10}})
    h = client(env).get(BASE + "/page").text
    row = re.search(r'<li class="row sun".*?</li>', h, re.S).group(0)
    assert "Chourouk" in row and "<time>" in row
    assert "Adhan" not in row and "Iqama" not in row and 'class="iq"' not in row
    assert "sunrise" not in _data(h)["days"][0]["iqama"]
    js = open(prayer_screen.__file__.replace("prayer_screen.py", "static/prayer-screen.js")).read()
    assert 'ORDER = ["fajr", "dhuhr", "asr", "maghrib", "isha"]' in js  # never a countdown target


def test_without_an_iqama_none_is_shown_nor_invented(env):
    _choose(env)
    h = client(env).get(BASE + "/page").text
    assert _data(h)["days"][0]["iqama"] == {}
    assert "ne publie pas d'iqama" in h and "noiqama" in h
    assert not re.search(r'class="iq"(?! hidden)', h)


def test_jumua_only_on_friday_and_only_if_entered(env):
    _choose(env)
    c = client(env)
    fri, sat = "2027-03-05", "2027-03-06"
    assert c.get(BASE + "/days?from=" + fri).json()["days"][0]["jumua"] == ["13:00", "14:15"]
    assert c.get(BASE + "/days?from=" + sat).json()["days"][0]["jumua"] == []
    _choose(env, jumua=[])
    public.reset()
    assert client(env).get(BASE + "/days?from=" + fri).json()["days"][0]["jumua"] == []


def test_days_gives_today_and_tomorrow_and_refuses_bad_dates(env):
    _choose(env)
    c = client(env)
    d = c.get(BASE + "/days?from=2027-03-09").json()
    assert [x["date"] for x in d["days"]] == ["2027-03-09", "2027-03-10"]
    assert d["days"][0]["hijri"] == "1 Chawwal 1448" and d["timezone"] == "Europe/Paris"
    assert d["days"][0]["gregorian"] == "Mardi 9 mars 2027"
    assert c.get(BASE + "/days?from=09-03-2027").status_code == 422
    assert c.get(BASE + "/days").json()["days"][0]["date"] == dt.date.today().isoformat()


def test_a_manual_day_without_times_says_so(env):
    _choose(env, "manual")
    h = client(env).get(BASE + "/page").text
    assert "Aucun horaire saisi pour ce jour." in h and _data(h)["days"][0]["times"] == {}


def test_the_month_table_and_pdf_stay_reachable(env):
    _choose(env)
    c = client(env)
    h = c.get(BASE + "/page").text
    month = dt.date.today().strftime("%Y-%m")
    assert f'href="{BASE}/page?month={month}"' in h and f"{BASE}/export?format=pdf&amp;month={month}" in h
    assert "<table>" in c.get(BASE + "/page?month=" + month).text


def test_everything_is_escaped_and_nothing_comes_from_a_third_party(env):
    _choose(env)
    set_mosque(env["conn"], env["mid"], name="</script><script>alert(1)</script> & Cie")
    h = client(env).get(BASE + "/page").text
    assert "<script>alert" not in h and "</script><script>alert" not in h and "&lt;/script&gt;" in h
    assert not re.search(r'(?:src|href)="https?://', h)
    assert "token" not in h.lower()


def test_assets_are_served_under_the_public_prefix_and_nothing_else(env):
    c = client(env)
    css = c.get("/api/v1/public/assets/prayer-screen.css")
    js = c.get("/api/v1/public/assets/prayer-screen.js")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css") and "--night" in css.text
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    fonts = c.get("/api/v1/public/assets/fonts.css")
    assert "/static/fonts/" not in fonts.text and "/api/v1/public/assets/fonts/Amiri" in fonts.text
    assert c.get("/api/v1/public/assets/fonts/Amiri-normal-400-arabic.woff2").headers["content-type"] == "font/woff2"
    for bad in ("../app.py", "fonts/../../app.py", "emblem.svg", "fonts/x.txt", "..%2fapp.py"):
        assert c.get("/api/v1/public/assets/" + bad).status_code == 404, bad


def test_a_mawaqit_or_private_mosque_has_no_screen(env):
    c = _choose(env)
    c.ecrire("/settings/prayer", {"source": "computed", "calc": CALC, "public": "0"}, methode="PUT")
    assert client(env).get(BASE + "/page").status_code == 404
    assert client(env).get(BASE + "/days").status_code == 404


def test_one_column_header_names_adhan_and_iqama_only_when_the_mosque_publishes_iqama(env):
    _choose(env, calc={**CALC, "iqama": {"isha": 10}})
    h = client(env).get(BASE + "/page").text
    cols = re.search(r'<div class="cols[^"]*" id="cols".*?</div>', h, re.S).group(0)
    assert "Adhan" in cols and "Iqama" in cols and "<span hidden>" not in cols
    _choose(env)
    cols = re.search(r'<div class="cols[^"]*" id="cols".*?</div>', client(env).get(BASE + "/page").text, re.S).group(0)
    assert "noiqama" in cols and "<span hidden>Iqama</span>" in cols


def test_the_phone_layout_is_sized_on_the_dynamic_viewport_with_safe_areas():
    css = (prayer_screen_css := __import__("pathlib").Path(prayer_screen.__file__).parent / "static" / "prayer-screen.css").read_text()
    phone = css[css.index("Phone, portrait"):]
    assert "100dvh" in phone and "safe-area-inset-bottom" in phone and "clamp(" in phone
    assert "http" not in phone and "url(" not in phone  # nothing external
