"""Renforts d'authentification : colmatage du rejeu de session, empreinte du navigateur, HSTS, cookies."""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import PW, client, env, login  # noqa: E402,F401  (conftest assure le chemin)

FF = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"


def test_empreinte_navigateur_tolerante_aux_versions():
    from minbar.backoffice import auth

    a = auth.ua_tag(FF)
    b = auth.ua_tag(FF.replace("130.0", "131.0"))
    assert a == b and len(a) == 16
    assert a != auth.ua_tag(CHROME)
    assert auth.ua_tag(None) == auth.ua_tag("")


def test_jeton_recopie_dans_un_autre_navigateur_ne_sert_a_rien(env):
    from fastapi.testclient import TestClient

    c = client(env, "admin", headers={"user-agent": FF})
    token = c.cookies.get("minbar_session")
    assert token

    autre = TestClient(env["app"], follow_redirects=False, headers={"user-agent": CHROME})
    autre.cookies.set("minbar_session", token)
    assert autre.get("/api/v1/session").status_code == 401

    # la vraie session n'est pas détruite par la tentative (pas de déni de service offert au voleur)
    assert c.get("/api/v1/session").status_code == 200
    from minbar.backoffice import db
    traces = db.all_(env["conn"], "SELECT * FROM audit WHERE action = 'session.navigateur_inattendu'")
    assert len(traces) == 1


def test_cookie_invalide_n_ouvre_rien_et_le_front_reste_servi(env):
    """Un cookie invalide ne casse pas l'affichage : le front se charge, l'API répond 401."""
    from fastapi.testclient import TestClient

    c = TestClient(env["app"], follow_redirects=False, headers={"user-agent": FF})
    c.cookies.set("minbar_session", "jeton-invente")
    assert c.get("/api/v1/session").status_code == 401
    assert c.get("/").status_code == 200


def test_cookie_de_session_httponly_samesite(env):
    c = client(env, "admin")
    setc = c.login_cookie
    assert "minbar_session=" in setc and "HttpOnly" in setc and "SameSite=lax" in setc
    assert "Path=/" in setc


def test_cookie_secure_derriere_un_proxy_https(env):
    """Derrière un proxy HTTPS, cookies Secure et HSTS ; le jar de test refuse d'envoyer un cookie
    Secure sur une URL http:// (un vrai navigateur, lui, est déjà en https), on le passe donc à la main."""
    from fastapi.testclient import TestClient

    c = TestClient(env["app"], follow_redirects=False, headers={"user-agent": FF, "x-forwarded-proto": "https"})
    pre = c.get("/api/v1/login-challenge").json()["pre"]
    r = c.post("/api/v1/login", data={"username": "admin", "password": PW, "pre": pre},
               cookies={"minbar_pre": pre})
    assert r.status_code == 200, r.text[:200]
    setc = r.headers.get("set-cookie", "")
    assert "Secure" in setc
    assert r.headers.get("strict-transport-security", "").startswith("max-age=31536000")
    # le HSTS ne doit pas s'imposer en HTTP local (sinon les autres postes du réseau perdent l'accès)
    plain = TestClient(env["app"], follow_redirects=False, headers={"user-agent": FF})
    assert "strict-transport-security" not in plain.get("/").headers


def test_entetes_de_securite_par_defaut(env):
    c = client(env)
    for url in ("/", "/api/v1/login-challenge"):
        h = c.get(url).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"], url
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    # la réponse JSON n'est jamais mise en cache
    assert c.get("/api/v1/login-challenge").headers["cache-control"] == "no-store"
