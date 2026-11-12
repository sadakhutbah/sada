"""Interface logo: separate network repository, validated upload, fallback to the neutral emblem."""

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client  # noqa: E402,F401  (conftest provides the path)


def _png(w=64, h=64, color=(15, 42, 25)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_le_depot_n_embarque_plus_le_logo_de_la_mosquee():
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "sada" / "backoffice" / "static"
    assert not (static / "logo.jpg").exists()
    assert (static / "emblem.svg").exists()


def _envoi(c, nom: str, donnees: bytes, type_mime: str):
    return c.request(
        "POST", "/api/v1/settings/logo", files={"logo": (nom, donnees, type_mime)}, headers={"X-CSRF": c.csrf}
    )


def test_sans_logo_embleme_neutre(env):
    c = client(env, "admin")
    d = c.get("/api/v1/settings").json()
    assert d["mosque"]["logo"] == "/static/emblem.svg"
    assert c.get("/static/emblem.svg").status_code == 200  # fallback served, even without a session


def test_upload_puis_retrait(env):
    c = client(env, "admin")
    up = _envoi(c, "mon-logo.png", _png(), "image/png")
    assert up.status_code == 200, up.text
    url = up.json()["logo"]
    assert url.startswith("/logo/ma-mosquee?v="), url
    assert c.get("/api/v1/session").json()["mosque"]["logo"] == url

    img = c.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG")

    assert c.request("DELETE", "/api/v1/settings/logo", headers={"X-CSRF": c.csrf}).status_code == 200
    assert c.get("/api/v1/session").json()["mosque"]["logo"] == "/static/emblem.svg"
    assert c.get(url).status_code == 404


def test_fichier_refuse(env):
    c = client(env, "admin")
    r = _envoi(c, "notes.txt", b"pas une image", "text/plain")
    assert r.status_code == 422 and "accepted format" in r.json()["message"]
    r = _envoi(c, "gros.png", b"\x89PNG" + b"0" * (3 * 1024 * 1024), "image/png")
    assert r.status_code == 422 and "too large" in r.json()["message"]
    assert c.get("/api/v1/settings").json()["mosque"]["logo"] == "/static/emblem.svg"


def test_upload_reserve_a_l_admin(env):
    c = client(env, "imam")
    assert _envoi(c, "x.png", _png(), "image/png").status_code == 403
