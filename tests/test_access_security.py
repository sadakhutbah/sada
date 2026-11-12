"""Authentication hardening: session replay patching, browser fingerprint, HSTS, cookies."""

import pytest

pytest.importorskip("fastapi")

from test_backoffice import PW, client, login  # noqa: E402,F401  (conftest provides the path)

FF = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"


def test_empreinte_navigateur_tolerante_aux_versions():
    from sada.backoffice import auth

    a = auth.ua_tag(FF)
    b = auth.ua_tag(FF.replace("130.0", "131.0"))
    assert a == b and len(a) == 16
    assert a != auth.ua_tag(CHROME)
    assert auth.ua_tag(None) == auth.ua_tag("")


def test_jeton_recopie_dans_un_autre_navigateur_ne_sert_a_rien(env):
    from fastapi.testclient import TestClient

    c = client(env, "admin", headers={"user-agent": FF})
    token = c.cookies.get("sada_session")
    assert token

    autre = TestClient(env["app"], follow_redirects=False, headers={"user-agent": CHROME})
    autre.cookies.set("sada_session", token)
    assert autre.get("/api/v1/session").status_code == 401

    # the real session is not destroyed by the attempt (no denial of service offered to the thief)
    assert c.get("/api/v1/session").status_code == 200
    from sada.backoffice import db

    traces = db.all_(env["conn"], "SELECT * FROM audit WHERE action = 'session.navigateur_inattendu'")
    assert len(traces) == 1


def test_cookie_invalide_n_ouvre_rien_et_le_front_reste_servi(env):
    """An invalid cookie does not break the display: the front end loads, the API answers 401."""
    from fastapi.testclient import TestClient

    c = TestClient(env["app"], follow_redirects=False, headers={"user-agent": FF})
    c.cookies.set("sada_session", "jeton-invente")
    assert c.get("/api/v1/session").status_code == 401
    assert c.get("/").status_code == 200


def test_cookie_de_session_httponly_samesite(env):
    c = client(env, "admin")
    setc = c.login_cookie
    assert "sada_session=" in setc and "HttpOnly" in setc and "SameSite=lax" in setc
    assert "Path=/" in setc


def test_cookie_secure_derriere_un_proxy_https(env):
    """Behind an HTTPS proxy, Secure cookies and HSTS; the test cookie jar refuses to send a Secure
    cookie over an http:// URL (a real browser is already on https), so we pass it by hand."""
    from fastapi.testclient import TestClient

    c = TestClient(env["app"], follow_redirects=False, headers={"user-agent": FF, "x-forwarded-proto": "https"})
    pre = c.get("/api/v1/login-challenge").json()["pre"]
    r = c.post("/api/v1/login", data={"username": "admin", "password": PW, "pre": pre}, cookies={"sada_pre": pre})
    assert r.status_code == 200, r.text[:200]
    setc = r.headers.get("set-cookie", "")
    assert "Secure" in setc
    assert r.headers.get("strict-transport-security", "").startswith("max-age=31536000")
    # HSTS must not impose itself on local HTTP (otherwise other machines on the network lose access)
    plain = TestClient(env["app"], follow_redirects=False, headers={"user-agent": FF})
    assert "strict-transport-security" not in plain.get("/").headers


def test_entetes_de_securite_par_defaut(env):
    c = client(env)
    for url in ("/", "/api/v1/login-challenge"):
        h = c.get(url).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"], url
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    # the JSON response is never cached
    assert c.get("/api/v1/login-challenge").headers["cache-control"] == "no-store"
