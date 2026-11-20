"""The public, read-only prayer-times endpoint: nothing private, nothing for Mawaqit, bounded."""

import datetime as dt

import pytest

pytest.importorskip("fastapi")

from conftest import client  # noqa: E402

from sada.backoffice import db, models, orm  # noqa: E402
from sada.backoffice.api import public  # noqa: E402

CALC = {"lat": "48.9", "lon": "2.3", "method": "uoif"}
URL = "/api/v1/public/mosques/ma-mosquee/prayer-times"
TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def _fresh_limits():
    public.reset()


def _choose(env, source, **extra):
    c = client(env, "admin")
    r = c.ecrire("/settings/prayer", {"source": source, "calc": CALC, "jumua": ["13:00"], **extra}, methode="PUT")
    assert r.status_code == 200, r.text
    return c


def test_computed_mosque_publishes_today_without_any_login(env):
    _choose(env, "computed", calc={**CALC, "iqama": {"isha": 10}})
    r = client(env).get(URL)  # no session at all
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mosque"] == {"slug": "ma-mosquee", "name": "Mosquée Exemple", "timezone": "Europe/Paris"}
    assert d["source"] == "computed" and d["jumua"] == ["13:00"] and d["date"] == TODAY.isoformat()
    assert set(d["times"]) == {"fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"} and set(d["iqama"]) == {"isha"}


def test_headers_cache_and_cors(env):
    _choose(env, "computed")
    h = client(env).get(URL).headers
    assert h["access-control-allow-origin"] == "*" and h["cache-control"] == "public, max-age=60"


def test_nothing_private_leaks(env):
    _choose(env, "computed")
    body = client(env).get(URL).text
    for secret in ("orchestrator", "token", "mawaqit_url", "output_dir", "hash", "password"):
        assert secret not in body.lower()


def test_a_date_and_a_month_and_bad_queries(env):
    _choose(env, "computed")
    c = client(env)
    assert c.get(URL + "?date=2027-03-21").json()["date"] == "2027-03-21"
    m = c.get(URL + "?month=2027-02").json()
    assert m["month"] == "2027-02" and len(m["days"]) == 28 and m["days"][0]["times"]["fajr"]
    for q in ("?date=21-03-2027", "?month=2027-13", "?date=x"):
        assert c.get(URL + q).status_code == 422


def test_manual_mosque_publishes_what_it_typed_and_a_day_without_times_is_empty(env):
    a = _choose(env, "manual")
    a.ecrire(
        "/settings/prayer/days",
        {"month": "2027-03", "days": [{"date": "2027-03-01", "fajr": "05:00", "isha_iqama": ""}]},
        methode="PUT",
    )
    c = client(env)
    assert c.get(URL + "?date=2027-03-01").json()["times"] == {"fajr": "05:00"}
    assert c.get(URL + "?date=2027-03-02").json()["times"] == {}
    assert [d["date"] for d in c.get(URL + "?month=2027-03").json()["days"]] == ["2027-03-01"]


def test_a_typed_correction_is_published_over_the_calculation(env):
    a = _choose(env, "computed")
    a.ecrire(
        "/settings/prayer/days", {"month": "2027-03", "days": [{"date": "2027-03-01", "isha": "23:30"}]}, methode="PUT"
    )
    assert client(env).get(URL + "?date=2027-03-01").json()["times"]["isha"] == "23:30"


def test_a_mawaqit_mosque_is_not_hosted(env):
    c = client(env, "admin")
    c.ecrire("/settings/prayer", {"source": "mawaqit", "mawaqit_url": "some-mosque-city"}, methode="PUT")
    r = client(env).get(URL)
    assert r.status_code == 404
    assert r.json()["source"] == "mawaqit" and r.json()["mawaqit_url"] == "https://mawaqit.net/fr/some-mosque-city"
    assert "times" not in r.json()
    assert client(env).get(URL + "?month=2027-03").status_code == 404  # no calendar either
    assert orm.rows(env["conn"], models.PrayerDay.__table__.select()) == []


def test_unknown_unchosen_or_switched_off_is_a_plain_404(env):
    c = client(env)
    assert c.get("/api/v1/public/mosques/nobody/prayer-times").status_code == 404
    assert c.get(URL).status_code == 404  # nothing chosen yet
    public.reset()
    a = _choose(env, "computed", public=False)
    assert c.get(URL).status_code == 404
    a.ecrire("/settings/prayer", {"source": "computed", "calc": CALC, "public": "1"}, methode="PUT")
    assert c.get(URL).status_code == 200  # a change shows at once, not a minute later


def test_the_slug_is_per_mosque(env):
    _choose(env, "computed")
    other = db.create_mosque(env["conn"], "other", "Other mosque")
    assert other
    assert client(env).get("/api/v1/public/mosques/other/prayer-times").status_code == 404  # nothing chosen there


def test_rate_limit_per_client(env, monkeypatch):
    _choose(env, "computed")
    c = client(env)
    codes = [c.get(URL).status_code for _n in range(public.RATE_PER_MINUTE + 3)]
    assert codes[: public.RATE_PER_MINUTE] == [200] * public.RATE_PER_MINUTE
    assert codes[-1] == 429
    r = c.get(URL)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_answers_are_cached_for_a_minute(env, monkeypatch):
    _choose(env, "computed")
    c = client(env)
    calls = []
    real = public.prayer_times.public_payload
    monkeypatch.setattr(public.prayer_times, "public_payload", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _n in range(5):
        assert c.get(URL).status_code == 200
    assert len(calls) == 1


# --- the visitor's real address behind the proxy -------------------------------------------------


class _Req:
    def __init__(self, peer, **headers):
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


def test_client_ip_reads_forwarded_for_only_from_a_trusted_proxy():
    from sada.backoffice.throttle import client_ip

    assert client_ip(_Req("10.42.0.7", x_forwarded_for="203.0.113.9")) == "203.0.113.9"
    assert client_ip(_Req("10.42.0.7", x_real_ip="203.0.113.9")) == "203.0.113.9"
    # a visitor reaching the app directly cannot choose its address
    assert client_ip(_Req("198.51.100.4", x_forwarded_for="203.0.113.9", x_real_ip="203.0.113.9")) == "198.51.100.4"
    # a value prepended by the visitor does not count: the right-most untrusted hop is the real one
    assert client_ip(_Req("10.42.0.7", x_forwarded_for="1.2.3.4, 203.0.113.9, 10.42.0.3")) == "203.0.113.9"
    assert client_ip(_Req("10.42.0.7", x_forwarded_for="not-an-ip")) == "10.42.0.7"
    assert client_ip(_Req("10.42.0.7")) == "10.42.0.7"
    assert client_ip(_Req(None)) == "?"


def test_client_ip_trusted_list_is_configurable(monkeypatch):
    from sada.backoffice.throttle import client_ip

    monkeypatch.setenv("SADA_TRUSTED_PROXIES", "192.0.2.0/24")
    assert client_ip(_Req("192.0.2.5", x_forwarded_for="203.0.113.9")) == "203.0.113.9"
    assert client_ip(_Req("10.42.0.7", x_forwarded_for="203.0.113.9")) == "10.42.0.7"
    monkeypatch.setenv("SADA_TRUSTED_PROXIES", "none")
    assert client_ip(_Req("192.0.2.5", x_forwarded_for="203.0.113.9")) == "192.0.2.5"


def test_rate_limit_counts_each_real_visitor_behind_the_proxy(env, monkeypatch):
    from fastapi.testclient import TestClient

    _choose(env, "computed")
    monkeypatch.setattr(public, "RATE_PER_MINUTE", 2)
    public.reset()
    proxy = TestClient(env["app"], client=("10.42.0.7", 5000))
    a, b = {"X-Forwarded-For": "203.0.113.1"}, {"X-Forwarded-For": "203.0.113.2"}
    assert [proxy.get(URL, headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert proxy.get(URL, headers=b).status_code == 200  # another visitor, same proxy: not throttled
    direct = TestClient(env["app"], client=("198.51.100.4", 5000))
    forged = [direct.get(URL, headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(3)]
    assert forged == [200, 200, 429]  # rotating the header does not escape the limit
