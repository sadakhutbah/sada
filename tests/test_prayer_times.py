"""Prayer times of a mosque: the three sources behind one resolver, the typed timetable, the CSV."""

import datetime as dt

import pytest

pytest.importorskip("fastapi")

from conftest import PW, client, one  # noqa: E402

from sada import mawaqit  # noqa: E402
from sada import prayer_calc as calc  # noqa: E402
from sada.backoffice import auth, db, models, orm, prayer_times  # noqa: E402

CALC = {"lat": "48.9", "lon": "2.3", "method": "uoif", "asr": "standard", "high_lats": "nightmiddle"}
TODAY = dt.date.today()


def _admin(env):
    return client(env, "admin")


def _mosque(env):
    return one(env["conn"], models.Mosque, id=env["mid"])


def _choose(c, source, **extra):
    body = {"source": source, "calc": CALC, "jumua": ["13:00", "14:00"], **extra}
    return c.ecrire("/settings/prayer", body, methode="PUT")


# ------------------------------------------------------------------ values


def test_time_values_are_normalised_and_checked():
    assert prayer_times.norm_time("7:05") == "07:05" and prayer_times.norm_time("07h05") == "07:05"
    assert prayer_times.norm_time("") == ""
    for bad in ("24:00", "12:60", "noon", "7"):
        with pytest.raises(ValueError):
            prayer_times.norm_time(bad)


def test_a_day_is_checked_as_a_whole():
    row = prayer_times.clean_day(
        {"fajr": "6:10", "dhuhr": "12:30", "asr": "15:00", "maghrib": "17:30", "isha": "19:00", "isha_iqama": "+15"}
    )
    assert row["fajr"] == "06:10" and row["isha_iqama"] == "19:15" and row["sunrise"] == ""
    assert prayer_times.clean_day({}) == {}
    with pytest.raises(ValueError):  # out of order
        prayer_times.clean_day({"fajr": "13:00", "dhuhr": "12:30"})
    with pytest.raises(ValueError):  # iqama before its adhan
        prayer_times.clean_day({"fajr": "06:00", "fajr_iqama": "05:00"})
    with pytest.raises(ValueError):  # a delay needs its prayer
        prayer_times.clean_day({"fajr_iqama": "+10"})
    assert prayer_times.clean_day({"isha": "23:50", "isha_iqama": "00:10"})["isha_iqama"] == "00:10"  # past midnight


def test_mawaqit_address_or_identifier_only():
    n = prayer_times.normalize_mawaqit
    assert n("some-mosque-city") == "https://mawaqit.net/fr/some-mosque-city"
    assert n("https://mawaqit.net/fr/m/some-mosque/") == "https://mawaqit.net/fr/m/some-mosque"
    assert n("mawaqit.net/fr/some-mosque") == "https://mawaqit.net/fr/some-mosque"
    assert n("") == ""
    for bad in (
        "http://mawaqit.net/fr/x",
        "https://evil.example/fr/x",
        "https://mawaqit.net.evil.example/x",
        "a b",
        "ab",
    ):
        with pytest.raises(ValueError):
            n(bad)


# ------------------------------------------------------------------ CSV


def test_csv_header_aliases_delimiters_and_dates():
    text = "Date;Fajr;Shuruq;Dhuhr;Asr;Maghrib;Isha;Isha_iqama\n01/03/2027;5:50;7:10;13:00;16:20;18:40;20:00;+10\n"
    days, errors = prayer_times.parse_csv(text)
    assert errors == [] and days["2027-03-01"]["sunrise"] == "07:10" and days["2027-03-01"]["isha_iqama"] == "20:10"
    # without a header: date then the five prayers
    days, errors = prayer_times.parse_csv("2027-03-02,05:48,13:00,16:21,18:41,20:01")
    assert errors == [] and days["2027-03-02"]["asr"] == "16:21"


def test_csv_errors_name_their_line_and_nothing_is_kept():
    days, errors = prayer_times.parse_csv(
        "date,fajr,dhuhr\n2027-03-01,05:50,13:00\nnot-a-date,05:50,13:00\n2027-03-03,25:00,13:00\n"
    )
    assert [e["line"] for e in errors] == [3, 4]
    assert prayer_times.parse_csv("")[1][0]["line"] == 0
    too_many = "date,fajr\n" + "\n".join("2027-01-01,05:00" for _n in range(prayer_times.MAX_IMPORT_ROWS + 5))
    assert prayer_times.parse_csv(too_many)[1]


# ------------------------------------------------------------------ resolver


def test_no_source_chosen_raises(env):
    with pytest.raises(prayer_times.NoTimes):
        prayer_times.times(env["conn"], _mosque(env))


def test_computed_source_gives_today_with_iqama_delay_and_jumua(env):
    c = _admin(env)
    r = _choose(c, "computed", calc={**CALC, "iqama": {"isha": 15}})
    assert r.status_code == 200, r.text
    d = prayer_times.times(env["conn"], _mosque(env))
    assert d["source"] == "computed" and d["jumua_list"] == ["13:00", "14:00"]
    assert len(d["times"]) == 5 and all(d["times"]) and d["iqama"][4] and not d["iqama"][0]
    from sada import prayers

    assert set(prayers.prayer_times(d)) == {"fajr", "dhuhr", "asr", "maghrib", "isha", "isha_iqama"}


def test_a_typed_day_corrects_the_calculation_only_for_that_day(env):
    c = _admin(env)
    _choose(c, "computed")
    month = TODAY.strftime("%Y-%m")
    calculated = prayer_times.times(env["conn"], _mosque(env))["times"]
    r = c.ecrire(
        "/settings/prayer/days", {"month": month, "days": [{"date": TODAY.isoformat(), "isha": "23:59"}]}, methode="PUT"
    )
    assert r.status_code == 200, r.text
    d = prayer_times.times(env["conn"], _mosque(env))
    assert d["times"][4] == "23:59" and d["times"][:4] == calculated[:4]


def test_manual_source_serves_only_what_was_typed(env):
    c = _admin(env)
    _choose(c, "manual")
    # nothing typed for today: only the jumu'a is known
    d = prayer_times.times(env["conn"], _mosque(env))
    assert d["times"] == [] and d["jumua_list"] == ["13:00", "14:00"]
    row = {
        "date": TODAY.isoformat(),
        "fajr": "05:00",
        "dhuhr": "13:00",
        "asr": "16:00",
        "maghrib": "19:00",
        "isha": "21:00",
    }
    assert (
        c.ecrire("/settings/prayer/days", {"month": TODAY.strftime("%Y-%m"), "days": [row]}, methode="PUT").status_code
        == 200
    )
    assert prayer_times.times(env["conn"], _mosque(env))["times"] == ["05:00", "13:00", "16:00", "19:00", "21:00"]
    # a half-typed day keeps the other prayers in place: nothing is invented, nothing shifts
    c.ecrire(
        "/settings/prayer/days",
        {"month": TODAY.strftime("%Y-%m"), "days": [{"date": TODAY.isoformat(), "asr": "16:00"}]},
        methode="PUT",
    )
    assert prayer_times.times(env["conn"], _mosque(env))["times"] == ["", "", "16:00", "", ""]


def test_manual_without_anything_raises(env):
    c = _admin(env)
    assert c.ecrire("/settings/prayer", {"source": "manual"}, methode="PUT").status_code == 200
    with pytest.raises(prayer_times.NoTimes):
        prayer_times.times(env["conn"], _mosque(env))


def test_mawaqit_source_goes_through_the_cache_and_is_never_copied(env):
    c = _admin(env)
    r = c.ecrire("/settings/prayer", {"source": "mawaqit", "mawaqit_url": "some-mosque-city"}, methode="PUT")
    assert r.status_code == 200, r.text
    assert _mosque(env)["mawaqit_url"] == "https://mawaqit.net/fr/some-mosque-city"
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return {"name": "X", "jumua": "13:15", "times": ["06:00", "12:30", "15:00", "17:30", "19:00"]}

    d = prayer_times.times(env["conn"], _mosque(env), fetch=fetch)
    assert d["source"] == "mawaqit" and d["jumua_list"] == ["13:15"] and d["times"][0] == "06:00"
    prayer_times.times(env["conn"], _mosque(env), fetch=fetch)
    assert len(calls) == 1  # the internal cache serves the live
    assert orm.rows(env["conn"], models.PrayerDay.__table__.select()) == []  # no timetable copied
    st = prayer_times.read(env["conn"], _mosque(env))
    assert st["public"] is False


def test_legacy_mosque_with_an_address_stays_on_mawaqit(env):
    from sada.backoffice import mawaqit_store  # noqa: F401

    db.update_mosque(env["conn"], env["mid"], mawaqit_url="https://mawaqit.net/fr/x")
    assert prayer_times.read(env["conn"], _mosque(env))["source"] == "mawaqit"


# ------------------------------------------------------------------ console API


def test_source_screen_data_and_rules(env):
    c = _admin(env)
    d = c.get("/api/v1/settings").json()["prayer"]
    assert d["source"] == "" and d["state"]["code"] == "prayer.unset"
    assert set(d["methods"]) >= {"uoif", "mwl", "isna", "egypt", "makkah", "custom"} and d["calc"]["method"] == "uoif"
    assert c.ecrire("/settings/prayer", {"source": "nope"}, methode="PUT").status_code == 422
    assert c.ecrire("/settings/prayer", {"source": "computed", "calc": {"lat": "x"}}, methode="PUT").status_code == 422
    assert c.ecrire("/settings/prayer", {"source": "mawaqit"}, methode="PUT").status_code == 422
    assert (
        c.ecrire(
            "/settings/prayer", {"source": "mawaqit", "mawaqit_url": "https://evil.example/x"}, methode="PUT"
        ).status_code
        == 422
    )
    assert _choose(c, "computed", jumua=["1", "2", "3", "4"]).status_code == 422
    assert _choose(c, "computed").status_code == 200
    d = c.get("/api/v1/settings").json()["prayer"]
    assert d["source"] == "computed" and d["public"] is True and d["state"]["level"] == "ok"


def test_only_an_admin_touches_prayer_times(env):
    for role in ("imam", "responsable"):
        c = client(env, role)
        assert c.ecrire("/settings/prayer", {"source": "manual"}, methode="PUT").status_code == 403
        assert c.ecrire("/settings/prayer/preview", {"calc": CALC}).status_code == 403
        assert c.get("/api/v1/settings/prayer/days").status_code == 403


def test_with_mawaqit_there_is_nothing_to_enter(env):
    c = _admin(env)
    c.ecrire("/settings/prayer", {"source": "mawaqit", "mawaqit_url": "some-mosque-city"}, methode="PUT")
    for r in (
        c.get("/api/v1/settings/prayer/days"),
        c.ecrire("/settings/prayer/days", {"month": "2027-03", "days": []}, methode="PUT"),
        c.ecrire("/settings/prayer/import", {"csv": "date,fajr\n2027-03-01,05:00"}),
        c.get("/api/v1/settings/prayer/export?year=2027"),
    ):
        assert r.status_code == 422 and "nothing to enter" in r.json()["message"]


def test_preview_writes_nothing(env):
    c = _admin(env)
    r = c.ecrire("/settings/prayer/preview", {"month": "2027-03", "calc": {**CALC, "iqama": {"fajr": 20}}})
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 31 and days[0]["date"] == "2027-03-01" and days[0]["fajr_iqama"] and days[0]["isha"]
    assert prayer_times.read(env["conn"], _mosque(env))["source"] == ""
    assert c.ecrire("/settings/prayer/preview", {"month": "2027-13", "calc": CALC}).status_code == 422
    assert c.ecrire("/settings/prayer/preview", {"month": "2027-03", "calc": {"lat": 200, "lon": 0}}).status_code == 422


def test_month_save_replaces_the_month_and_checks_everything_first(env):
    c = _admin(env)
    _choose(c, "manual")
    rows = [{"date": f"2027-03-{d:02d}", "fajr": "05:00", "dhuhr": "13:00"} for d in (1, 2, 3)]
    assert c.ecrire("/settings/prayer/days", {"month": "2027-03", "days": rows}, methode="PUT").status_code == 200
    # one bad row: refused whole, the stored month is untouched
    bad = [{"date": "2027-03-01", "fajr": "05:10"}, {"date": "2027-03-02", "fajr": "13:00", "dhuhr": "12:00"}]
    r = c.ecrire("/settings/prayer/days", {"month": "2027-03", "days": bad}, methode="PUT")
    assert r.status_code == 422 and "2027-03-02" in r.json()["message"]
    assert len(c.get("/api/v1/settings/prayer/days?month=2027-03").json()["days"]) == 31
    stored = prayer_times.stored(env["conn"], env["mid"], "2027-03-01", "2027-03-31")
    assert sorted(stored) == ["2027-03-01", "2027-03-02", "2027-03-03"] and stored["2027-03-01"]["fajr"] == "05:00"
    # a day outside the month is refused; replacing with fewer days drops the others
    assert (
        c.ecrire(
            "/settings/prayer/days",
            {"month": "2027-03", "days": [{"date": "2027-04-01", "fajr": "05:00"}]},
            methode="PUT",
        ).status_code
        == 422
    )
    c.ecrire("/settings/prayer/days", {"month": "2027-03", "days": rows[:1]}, methode="PUT")
    assert sorted(prayer_times.stored(env["conn"], env["mid"], "2027-03-01", "2027-03-31")) == ["2027-03-01"]


def test_csv_import_and_export_round_trip(env):
    c = _admin(env)
    _choose(c, "manual")
    text = "date,fajr,sunrise,dhuhr,asr,maghrib,isha,isha_iqama\n2027-03-01,05:50,07:10,13:00,16:20,18:40,20:00,+10\n2027-03-02,05:48,07:08,13:00,16:21,18:41,20:01,\n"
    r = c.ecrire("/settings/prayer/import", {"csv": text})
    assert r.status_code == 200 and r.json()["imported"] == 2, r.text
    out = c.get("/api/v1/settings/prayer/export?year=2027")
    assert out.status_code == 200 and out.headers["content-type"].startswith("text/csv")
    assert (
        out.text.splitlines()[0].startswith("date,fajr,sunrise")
        and "2027-03-01,05:50,07:10,13:00,16:20,18:40,20:00" in out.text
    )
    assert out.text.count("\n") == 3  # a manual timetable exports only the days it has
    again = c.ecrire("/settings/prayer/import", {"csv": out.text})
    assert again.status_code == 200 and again.json()["imported"] == 2
    bad = c.ecrire("/settings/prayer/import", {"csv": "date,fajr\n2027-03-05,05:00\n2027-03-06,xx"})
    assert bad.status_code == 422 and "Line" in bad.json()["message"]
    assert "2027-03-05" not in prayer_times.stored(
        env["conn"], env["mid"], "2027-03-01", "2027-03-31"
    )  # all or nothing


def test_a_computed_year_exports_as_a_timetable_that_can_be_taken_over(env):
    c = _admin(env)
    _choose(c, "computed")
    out = c.get("/api/v1/settings/prayer/export?year=2027")
    assert out.text.count("\n") == 366  # header + 365 days
    _choose(c, "manual")
    assert c.ecrire("/settings/prayer/import", {"csv": out.text}).json()["imported"] == 365
    assert len(prayer_times.stored(env["conn"], env["mid"], "2027-01-01", "2027-12-31")) == 365


def test_deduce_reads_mawaqit_once_and_stores_nothing(env, monkeypatch):
    c = _admin(env)
    truth = calc.Params(lat=48.9, lon=2.3, timezone="Europe/Paris", method="isna")
    cal = [{} for _m in range(12)]
    year = TODAY.year
    for day, t in calc.year_times(year, truth).items():
        d = dt.date.fromisoformat(day)
        cal[d.month - 1][str(d.day)] = [t[k] for k in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")]
    seen = []

    def fake(url, timeout=15.0, get=None):
        seen.append(url)
        return {"calendar": cal, "lat": 48.9, "lon": 2.3, "timezone": "Europe/Paris", "name": "x"}

    monkeypatch.setattr(mawaqit, "fetch_calendar", fake)
    r = c.ecrire("/settings/prayer/deduce", {"mawaqit_url": "some-mosque-city"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert seen == ["https://mawaqit.net/fr/some-mosque-city"]
    assert d["calc"]["method"] == "isna" and d["worst"] == 0 and d["calc"]["lat"] == 48.9
    assert prayer_times.read(env["conn"], _mosque(env))["source"] == ""  # a suggestion: nothing saved
    assert orm.rows(env["conn"], models.PrayerDay.__table__.select()) == []
    assert c.ecrire("/settings/prayer/deduce", {"mawaqit_url": "https://evil.example/x"}).status_code == 422
    assert c.ecrire("/settings/prayer/deduce", {}).status_code == 422  # no address at all


def test_state_of_a_manual_timetable(env):
    c = _admin(env)
    _choose(c, "manual")
    assert prayer_times.state(env["conn"], _mosque(env))["code"] == "prayer.manual_today_missing"
    rows = [{"date": (TODAY + dt.timedelta(days=i)).isoformat(), "fajr": "05:00"} for i in range(3)]
    for month in {r["date"][:7] for r in rows}:
        c.ecrire(
            "/settings/prayer/days",
            {"month": month, "days": [r for r in rows if r["date"][:7] == month]},
            methode="PUT",
        )
    s = prayer_times.state(env["conn"], _mosque(env))
    assert s["code"] == "prayer.manual_running_out" and s["level"] == "attention"


# ------------------------------------------------------------------ the engine's door and isolation


def test_orchestrator_reads_the_chosen_source_through_the_internal_door(env):
    from sada.backoffice import internal

    c = _admin(env)
    _choose(c, "computed")
    token = internal.new_agent_token(env["conn"], env["mid"])
    r = client(env).get("/api/v1/internal/times", headers={internal.HEADER: token})
    assert r.status_code == 200, r.text
    body = r.json()["times"]
    assert body["source"] == "computed" and body["jumua_list"] == ["13:00", "14:00"] and len(body["times"]) == 5
    from sada import prayers

    assert prayers.from_backoffice("http://bo", 0, fetch=lambda u, t: r.json())["jumua_list"] == ["13:00", "14:00"]


def test_each_mosque_has_its_own_source_and_timetable(env):
    other = db.create_mosque(env["conn"], "other", "Other mosque")
    auth.create_user(env["conn"], other, "admin2", PW, "admin")
    a, b = _admin(env), client(env, "admin2")
    _choose(a, "manual")
    _choose(b, "computed", jumua=["12:00"])
    a.ecrire(
        "/settings/prayer/days", {"month": "2027-03", "days": [{"date": "2027-03-01", "fajr": "05:00"}]}, methode="PUT"
    )
    assert prayer_times.stored(env["conn"], other, "2027-03-01", "2027-03-31") == {}
    assert prayer_times.read(env["conn"], _mosque(env))["jumua"] == ["13:00", "14:00"]
    assert prayer_times.read(env["conn"], {"id": other})["jumua"] == ["12:00"]
    assert b.get("/api/v1/settings/prayer/days?month=2027-03").json()["days"][0]["set"]["fajr"] == ""
    assert a.get("/api/v1/settings/prayer/days?month=2027-03").json()["days"][0]["set"]["fajr"] == "05:00"
