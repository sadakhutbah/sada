"""L'adresse publique affichée ne doit pas être celle de la machine."""

from __future__ import annotations

import pytest

from sada.backoffice import instance
from test_backoffice import client, csrf, env, login  # noqa: F401


def test_sans_reglage_l_adresse_vient_de_la_requete(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "i.json"))
    assert instance.base("http://127.0.0.1:8801/") == "http://127.0.0.1:8801"


def test_l_adresse_saisie_remplace_celle_de_la_requete(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "i.json"))
    instance.enregistrer("https://sada.exemple.org/")
    assert instance.public_url() == "https://sada.exemple.org"
    assert instance.base("http://127.0.0.1:8801") == "https://sada.exemple.org"


def test_une_adresse_sans_schema_est_refusee(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "i.json"))
    with pytest.raises(ValueError):
        instance.enregistrer("sada.exemple.org")


def test_le_reglage_se_vide(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "i.json"))
    instance.enregistrer("https://sada.exemple.org")
    instance.enregistrer("")
    assert instance.public_url() == ""
    assert instance.base("http://127.0.0.1:8801") == "http://127.0.0.1:8801"


def test_un_fichier_illisible_ne_casse_rien(monkeypatch, tmp_path):
    f = tmp_path / "i.json"
    f.write_text("pas du json", encoding="utf-8")
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(f))
    assert instance.lire() == {}
    assert instance.public_url() == ""


def test_l_adresse_publique_alimente_les_liens_de_documentation(env, monkeypatch, tmp_path):
    """L'administrateur doit voir l'adresse qu'il a saisie, pas celle du serveur."""
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "i.json"))
    c = client(env, username="admin")
    r = c.put("/api/v1/settings/instance", json={"public_url": "https://sada.exemple.org"},
              headers={"X-CSRF": csrf(c)})
    assert r.status_code == 200, r.text
    reglages = c.get("/api/v1/settings").json()
    assert reglages["guide"]["human"] == "https://sada.exemple.org/docs/ecrans-mode-emploi"
    assert reglages["guide"]["docs"] == "https://sada.exemple.org/docs"
    assert reglages["guide"]["public_url"] == "https://sada.exemple.org"


def test_une_adresse_invalide_est_refusee_par_l_api(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_INSTANCE_FILE", str(tmp_path / "i.json"))
    c = client(env, username="admin")
    r = c.put("/api/v1/settings/instance", json={"public_url": "pas une adresse"},
              headers={"X-CSRF": csrf(c)})
    assert r.status_code == 422, r.text
    assert "http" in r.json()["message"]
