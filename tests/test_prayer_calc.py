"""Calculated prayer times: the settings map onto the library, and the fit finds them back."""

import datetime as dt

import pytest

from sada import prayer_calc as calc
from sada import prayer_fit

PARIS = {"lat": 48.9, "lon": 2.3, "timezone": "Europe/Paris"}  # a fictional place, not a mosque


def test_reference_day_of_the_library_manual():
    """The PrayTimes.org manual's own example (ISNA, Waterloo, 9 Feb 2011): sunrise 07:26."""
    p = calc.Params(lat=43, lon=-80, timezone="America/Toronto", method="isna")
    t = calc.day_times(dt.date(2011, 2, 9), p)
    assert t["sunrise"] == "07:26"
    assert set(t) == set(calc.OUTPUT) and all(len(v) == 5 for v in t.values())


def test_daylight_saving_is_followed():
    p = calc.Params(**PARIS)
    winter, summer = calc.day_times(dt.date(2027, 1, 15), p), calc.day_times(dt.date(2027, 7, 15), p)
    h = lambda t: int(t[:2]) * 60 + int(t[3:])  # noqa: E731
    assert 50 <= h(summer["dhuhr"]) - h(winter["dhuhr"]) <= 70  # solar noon moves by the hour of the clock change


def test_method_changes_fajr_and_asr_school_changes_asr():
    day = dt.date(2027, 3, 21)
    uoif = calc.day_times(day, calc.Params(**PARIS, method="uoif"))
    mwl = calc.day_times(day, calc.Params(**PARIS, method="mwl"))
    assert uoif["fajr"] > mwl["fajr"] and uoif["isha"] < mwl["isha"]  # 12 degrees: later fajr, earlier isha
    hanafi = calc.day_times(day, calc.Params(**PARIS, asr="hanafi"))
    assert hanafi["asr"] > uoif["asr"]


def test_makkah_isha_is_a_delay_after_maghrib():
    t = calc.day_times(dt.date(2027, 3, 21), calc.Params(lat=21.4, lon=39.8, timezone="Asia/Riyadh", method="makkah"))
    h = lambda s: int(s[:2]) * 60 + int(s[3:])  # noqa: E731
    assert h(t["isha"]) - h(t["maghrib"]) == 90  # the PyPI port of PrayTimes subtracts it: handled here


def test_offsets_and_bounds_apply_after_the_calculation():
    day = dt.date(2027, 1, 15)
    base = calc.day_times(day, calc.Params(**PARIS))
    shifted = calc.day_times(day, calc.Params(**PARIS, offsets={"dhuhr": 5, "maghrib": -2}))
    assert shifted["dhuhr"] > base["dhuhr"] and shifted["maghrib"] < base["maghrib"] and shifted["fajr"] == base["fajr"]
    floor = calc.day_times(day, calc.Params(**PARIS, bounds={"dhuhr": ("13:00", ""), "isha": ("", "18:00")}))
    assert floor["dhuhr"] == "13:00" and floor["isha"] == "18:00"


def test_isha_after_midnight_is_late_not_early():
    p = calc.Params(**PARIS, method="mwl")
    june = calc.day_times(dt.date(2027, 6, 21), p)
    capped = calc.day_times(dt.date(2027, 6, 21), calc.Params(**PARIS, method="mwl", bounds={"isha": ("", "23:55")}))
    assert june["isha"] < "05:00" or june["isha"] > "23:55" or june["isha"] == ""  # past midnight or never reached
    assert capped["isha"] == "23:55" or capped["isha"] == ""


@pytest.mark.parametrize(
    "bad",
    [
        {"lat": 95, "lon": 0},
        {"lat": 10, "lon": 200},
        {"lat": 10, "lon": 10, "method": "nope"},
        {"lat": 10, "lon": 10, "asr": "nope"},
        {"lat": 10, "lon": 10, "high_lats": "nope"},
        {"lat": 10, "lon": 10, "timezone": "Mars/Olympus"},
        {"lat": 10, "lon": 10, "method": "custom", "fajr_angle": 40},
        {"lat": 10, "lon": 10, "offsets": {"fajr": 90}},
        {"lat": 10, "lon": 10, "bounds": {"isha": ("25:00", "")}},
    ],
)
def test_invalid_settings_are_refused(bad):
    with pytest.raises(calc.InvalidParams):
        calc.check(calc.Params(**bad))


def test_params_from_text_values():
    p = calc.params_from(
        {
            "lat": "48,9",
            "lon": "2.3",
            "method": "custom",
            "fajr_angle": "13",
            "isha_angle": "14",
            "offsets": {"asr": "1", "fajr": 0},
        },
        "Europe/Paris",
    )
    assert (p.lat, p.fajr_angle, p.offsets) == (48.9, 13.0, {"asr": 1})
    with pytest.raises(calc.InvalidParams):
        calc.params_from({"lat": "", "lon": "2"}, "Europe/Paris")


def _published(p: calc.Params, year: int) -> list[dict]:
    """A Mawaqit-style calendar (12 months of {day: [fajr, sunrise, dhuhr, asr, maghrib, isha]})."""
    cal: list[dict] = [{} for _m in range(12)]
    for day, t in calc.year_times(year, p).items():
        d = dt.date.fromisoformat(day)
        cal[d.month - 1][str(d.day)] = [t[k] for k in prayer_fit.ORDER]
    return cal


def test_fit_finds_back_the_settings_of_a_synthetic_calendar():
    """Round trip on made-up data: angles, offsets and a floor typed by the mosque are all recovered."""
    truth = calc.Params(
        **PARIS,
        method="custom",
        fajr_angle=13.0,
        isha_angle=14.0,
        offsets={"dhuhr": 4, "maghrib": 2},
        bounds={"isha": ("19:30", "")},
    )
    found = prayer_fit.deduce(_published(truth, 2027), PARIS["lat"], PARIS["lon"], PARIS["timezone"], 2027)
    got = found["params"]
    assert got["fajr_angle"] == 13.0 and abs(got["isha_angle"] - 14.0) <= 0.2  # days held by the floor blur a tenth
    assert {k: got["offsets"][k] for k in ("dhuhr", "maghrib")} == {"dhuhr": 4, "maghrib": 2}
    assert got["bounds"]["isha"][0] == "19:30" and abs(got["offsets"].get("isha", 0)) <= 1
    assert found["worst"] <= 1 and all(e["within1"] == 100.0 for e in found["errors"].values())


def test_fit_names_a_known_method_and_refuses_a_partial_year():
    truth = calc.Params(**PARIS, method="uoif")
    found = prayer_fit.deduce(_published(truth, 2027), PARIS["lat"], PARIS["lon"], PARIS["timezone"], 2027)
    assert found["params"]["method"] == "uoif" and found["worst"] == 0
    with pytest.raises(calc.InvalidParams):
        prayer_fit.deduce(_published(truth, 2027)[:2], PARIS["lat"], PARIS["lon"], PARIS["timezone"], 2027)
