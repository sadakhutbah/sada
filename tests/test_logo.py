"""Logo de l'interface : dépôt réseau distinct, upload validé, repli sur l'emblème neutre."""
import re

import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, csrf, env  # noqa: E402,F401  (conftest assure le chemin)


def _png(w=64, h=64, color=(15, 42, 25)) -> bytes:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_le_depot_n_embarque_plus_le_logo_de_la_mosquee():
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "minbar" / "backoffice" / "static"
    assert not (static / "logo.jpg").exists()
    assert (static / "emblem.svg").exists()


def test_sans_logo_embleme_neutre_dans_la_barre_et_l_onglet(env):
    c = client(env, "admin")
    page = c.get("/admin/mosquee").text
    assert 'href="/static/emblem.svg"' in page          # favicon
    assert 'src="/static/emblem.svg"' in page           # barre
    assert "logo.jpg" not in page


def test_upload_puis_retrait(env):
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosquee")
    up = c.post("/admin/logo", data={"csrf": tok}, files={"logo": ("mon-logo.png", _png(), "image/png")})
    assert up.status_code == 303, up.text

    page = c.get("/admin/mosquee").text
    url = re.search(r'class="brand"[^>]*><img src="([^"]+)"', page).group(1)
    assert url.startswith("/logo/ma-mosquee?v="), url
    img = c.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG")

    tok = csrf(c, "/admin/mosquee")
    c.post("/admin/logo/supprimer", data={"csrf": tok})
    page = c.get("/admin/mosquee").text
    assert 'src="/static/emblem.svg"' in page
    assert c.get(url).status_code == 404


def test_fichier_refuse(env):
    c = client(env, "admin")
    tok = csrf(c, "/admin/mosquee")
    r = c.post("/admin/logo", data={"csrf": tok}, files={"logo": ("notes.txt", b"pas une image", "text/plain")})
    assert r.status_code == 303
    page = c.get("/admin/mosquee").text
    assert "Format accepté" in page
    # fichier trop lourd
    tok = csrf(c, "/admin/mosquee")
    c.post("/admin/logo", data={"csrf": tok}, files={"logo": ("gros.png", b"\x89PNG" + b"0" * (3 * 1024 * 1024), "image/png")})
    assert "trop lourde" in c.get("/admin/mosquee").text


def test_upload_reserve_a_l_admin(env):
    c = client(env, "imam")
    tok = csrf(c, "/compte")
    r = c.post("/admin/logo", data={"csrf": tok}, files={"logo": ("x.png", _png(), "image/png")})
    assert r.status_code == 403
