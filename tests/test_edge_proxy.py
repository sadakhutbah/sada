"""Behind the Cloudflare proxy: visitor address, ingest host, public base URL, hosts, cache headers."""

import pytest

pytest.importorskip("fastapi")

from conftest import client  # noqa: E402

from sada.backoffice import edge, live_input, models, orm  # noqa: E402
from sada.backoffice.api import public  # noqa: E402
from sada.backoffice.throttle import client_ip, peer_trusted  # noqa: E402

CF = "172.69.10.5"  # inside Cloudflare's 172.64.0.0/13
VISITOR = "203.0.113.9"
TRAEFIK = "10.42.0.7"
CALC = {"lat": "48.9", "lon": "2.3", "method": "uoif"}
URL = "/api/v1/public/mosques/{slug}/prayer-times"


class _Req:
    def __init__(self, peer, **headers):
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


# --- visitor address ----------------------------------------------------------------------------


def test_cf_connecting_ip_is_believed_behind_a_cloudflare_hop():
    req = _Req(TRAEFIK, x_forwarded_for=CF, cf_connecting_ip=VISITOR)
    assert client_ip(req) == VISITOR
    # the visitor's own X-Forwarded-For prefix does not matter either
    assert client_ip(_Req(TRAEFIK, x_forwarded_for=f"1.2.3.4, {CF}", cf_connecting_ip=VISITOR)) == VISITOR


def test_cf_connecting_ip_is_ignored_when_the_request_did_not_come_through_cloudflare():
    # reaching the origin through Traefik without Cloudflare: the header is a forgery
    assert client_ip(_Req(TRAEFIK, x_forwarded_for="198.51.100.4", cf_connecting_ip=VISITOR)) == "198.51.100.4"
    # no forwarded chain to prove an edge hop: ignored as well
    assert client_ip(_Req(TRAEFIK, cf_connecting_ip=VISITOR)) == TRAEFIK
    # not a trusted proxy at all: nothing is read
    assert client_ip(_Req("198.51.100.4", x_forwarded_for=CF, cf_connecting_ip=VISITOR)) == "198.51.100.4"


def test_a_cloudflare_hop_without_the_header_resolves_to_the_visitor_not_the_edge():
    assert client_ip(_Req(TRAEFIK, x_forwarded_for=f"{VISITOR}, {CF}")) == VISITOR
    assert client_ip(_Req(TRAEFIK, x_forwarded_for=CF, cf_connecting_ip="garbage")) == TRAEFIK


def test_cloudflare_ranges_are_configurable(monkeypatch):
    monkeypatch.setenv("SADA_CLOUDFLARE_PROXIES", "none")
    assert client_ip(_Req(TRAEFIK, x_forwarded_for=CF, cf_connecting_ip=VISITOR)) == CF
    monkeypatch.setenv("SADA_CLOUDFLARE_PROXIES", "198.51.100.0/24")
    assert client_ip(_Req(TRAEFIK, x_forwarded_for="198.51.100.4", cf_connecting_ip=VISITOR)) == VISITOR


def test_forwarded_proto_is_believed_only_from_a_trusted_proxy():
    from sada.backoffice.context import _sec

    class R(_Req):
        url = type("U", (), {"scheme": "http"})()

    assert _sec(R(TRAEFIK, x_forwarded_proto="https")) is True
    assert _sec(R("198.51.100.4", x_forwarded_proto="https")) is False
    assert peer_trusted(_Req(TRAEFIK)) and not peer_trusted(_Req("198.51.100.4")) and not peer_trusted(_Req(None))


# --- ingest host --------------------------------------------------------------------------------


def test_ingest_host_setting_default_and_fallback(monkeypatch):
    monkeypatch.delenv("SADA_INGEST_HOST", raising=False)
    monkeypatch.delenv("INGEST_HOST", raising=False)
    monkeypatch.delenv("SADA_MODE", raising=False)
    assert edge.ingest_host("192.168.1.5") == "192.168.1.5"  # local install: the host the browser used
    assert edge.ingest_host("") == "127.0.0.1"
    monkeypatch.setenv("SADA_MODE", "hosted")
    assert edge.ingest_host("console.sadakhutbah.org") == "ingest.sadakhutbah.org"
    monkeypatch.setenv("INGEST_HOST", "in.example.org")
    assert edge.ingest_host("x") == "in.example.org"
    monkeypatch.setenv("SADA_INGEST_HOST", "live.example.org")
    assert edge.ingest_host("x") == "live.example.org"


def test_rtmp_and_srt_share_the_ingest_host_everywhere(env, monkeypatch):
    monkeypatch.setenv("SADA_INGEST_HOST", "ingest.example.org")
    c = client(env, "admin")
    r = c.ecrire("/settings/input", {"mode": "push"}, methode="PUT")
    assert r.status_code == 200, r.text
    mosque = orm.first(env["conn"], models.Mosque, id=env["mid"])
    s = live_input.slug(mosque)
    d = r.json()["input"]
    assert d["push_rtmp"] == f"rtmp://ingest.example.org:1935/live/{s}"
    assert d["push_srt"] == f"srt://ingest.example.org:8890?streamid=publish:live/{s}"
    assert c.get("/api/v1/settings").json()["input"]["push_srt"] == d["push_srt"]

    k = c.ecrire("/settings/input/key", {}).json()
    assert k["obs_server"] == "rtmp://ingest.example.org:1935/live"
    assert k["obs_key"] == f"{s}?user={s}&pass={k['key']}"
    assert k["obs_srt"] == f"srt://ingest.example.org:8890?streamid=publish:live/{s}:{s}:{k['key']}"
    # the stored key is never part of what the screen reads again
    assert k["key"] not in str(c.get("/api/v1/settings").json())


# --- public base URL ----------------------------------------------------------------------------


def test_public_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "instance.json"))
    monkeypatch.delenv("SADA_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert edge.public_base("http://10.0.0.1:8090/") == "http://10.0.0.1:8090"
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sadakhutbah.org/")
    assert edge.public_base("http://10.0.0.1:8090") == "https://sadakhutbah.org"
    assert edge.public_page_url("al-nour") == "https://sadakhutbah.org/api/v1/public/mosques/al-nour/prayer-times/page"
    monkeypatch.setenv("SADA_PUBLIC_BASE_URL", "not a url")  # ignored: falls back
    monkeypatch.delenv("PUBLIC_BASE_URL")
    assert edge.public_base("http://10.0.0.1:8090") == "http://10.0.0.1:8090"


def _own_timetable(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/prayer", {"source": "computed", "calc": CALC, "jumua": ["13:00"]}, methode="PUT")
    assert r.status_code == 200, r.text
    return c


def test_pdf_qr_uses_the_public_base_url(env, monkeypatch):
    seen = []
    from sada.backoffice import prayer_export

    def fake(conn, mosque, first, last, base_url="", lang="fr"):
        seen.append(base_url)
        return b"%PDF"

    monkeypatch.setattr(prayer_export, "pdf_bytes", fake)
    c = _own_timetable(env)
    monkeypatch.setenv("SADA_PUBLIC_BASE_URL", "https://sadakhutbah.org")
    assert c.get("/api/v1/settings/prayer/export?format=pdf&month=2027-03").status_code == 200
    public_pdf = "/api/v1/public/mosques/ma-mosquee/prayer-times/export?format=pdf&month=2027-03"
    assert client(env).get(public_pdf).status_code == 200
    assert seen == ["https://sadakhutbah.org", "https://sadakhutbah.org"]


# --- hosts --------------------------------------------------------------------------------------


def test_hosts_open_by_default_and_restricted_when_set(monkeypatch):
    monkeypatch.delenv("SADA_ALLOWED_HOSTS", raising=False)
    assert edge.host_allowed("whatever", "/api/v1/settings")
    monkeypatch.setenv("SADA_ALLOWED_HOSTS", "console.sadakhutbah.org, backoffice")
    monkeypatch.setenv("SADA_PUBLIC_HOSTS", "sadakhutbah.org")
    assert edge.host_allowed("console.sadakhutbah.org", "/api/v1/settings")
    assert edge.host_allowed("Console.SadaKhutbah.org:443", "/")
    assert edge.host_allowed("backoffice:8090", "/internal")
    assert edge.host_allowed("sadakhutbah.org", "/api/v1/public/mosques/x/prayer-times")
    assert not edge.host_allowed("sadakhutbah.org", "/api/v1/settings")  # the apex serves the public API only
    assert not edge.host_allowed("evil.example", "/api/v1/public/mosques/x/prayer-times")


def test_the_app_refuses_an_unknown_host_but_not_the_probe(env, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SADA_ALLOWED_HOSTS", "console.example.org")
    monkeypatch.setenv("SADA_PUBLIC_HOSTS", "example.org")
    c = TestClient(env["app"])
    assert c.get("/health", headers={"Host": "10.42.0.9:8090"}).status_code == 200
    assert c.get("/health", headers={"Host": "evil.test"}).status_code == 200
    r = c.get("/api/live", headers={"Host": "evil.test"})
    assert r.status_code == 421
    assert c.get("/api/live", headers={"Host": "example.org"}).status_code == 421
    assert c.get("/api/live", headers={"Host": "console.example.org"}).status_code != 421
    pub = URL.format(slug="nobody")
    assert c.get(pub, headers={"Host": "example.org"}).status_code == 404  # served, the mosque is unknown
    assert c.get(pub, headers={"Host": "evil.test"}).status_code == 421


# --- cache headers on the public answers --------------------------------------------------------


def test_public_cache_control_is_shareable_for_a_minute():
    cc = edge.public_cache_control()
    assert "public" in cc and "max-age=60" in cc and "s-maxage=60" in cc and "stale-while-revalidate" in cc


def test_public_answers_cache_headers(env):
    public.reset()
    _own_timetable(env)
    pub = client(env)
    base = URL.format(slug="ma-mosquee")
    for path in (base, base + "/page", base + "/export?format=csv&month=2027-03"):
        r = pub.get(path)
        assert r.status_code == 200, (path, r.text)
        assert r.headers["cache-control"] == edge.public_cache_control(), path
    assert "Accept-Language" in pub.get(base + "/page").headers["vary"]
    missing = pub.get(URL.format(slug="nobody-here"))
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
    assert missing.headers["access-control-allow-origin"] == "*"
    # the console never lets the shared caches keep its pages
    assert pub.get("/api/live").headers["cache-control"] == "no-store"


def test_tunnel_without_traefik_counts_the_visitor_not_cloudflared():
    # Cloudflare Tunnel: the peer is the cloudflared pod, X-Forwarded-For is the visitor alone (no edge hop).
    pod = "10.42.3.8"
    assert client_ip(_Req(pod, x_forwarded_for=VISITOR, cf_connecting_ip=VISITOR)) == VISITOR
    assert client_ip(_Req(pod, x_forwarded_for=f"1.2.3.4, {VISITOR}", cf_connecting_ip=VISITOR)) == VISITOR
    # reaching the origin from outside the cluster network: nothing is believed
    assert client_ip(_Req("198.51.100.4", x_forwarded_for=VISITOR, cf_connecting_ip="1.1.1.1")) == "198.51.100.4"
