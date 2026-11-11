"""Le navigateur de rendu fourni avec l'application : présent, téléchargeable, trouvé.

Ces tests ne téléchargent rien : ils vérifient comment le bon exécutable est choisi, et qu'un
téléchargement manquant donne un message qui dit quoi faire plutôt que « ça ne marche pas ».
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sada.backoffice import template_render as tr


def test_le_navigateur_fourni_prime_sur_celui_de_la_machine(tmp_path, monkeypatch):
    embarque = tmp_path / "chrome-headless-shell-linux64" / "chrome-headless-shell"
    embarque.parent.mkdir(parents=True)
    embarque.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tr, "EMBEDDED", tmp_path)
    assert tr.browser_path() == str(embarque)


def test_sans_navigateur_fourni_on_prend_celui_de_la_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "EMBEDDED", tmp_path / "vide")
    monkeypatch.setattr(tr, "CHROMIUM_CANDIDATES", ("/usr/bin/chromium",))
    if not Path("/usr/bin/chromium").is_file():
        pytest.skip("aucun Chromium installé sur cette machine")
    assert tr.browser_path() == "/usr/bin/chromium"


def test_sans_aucun_navigateur_le_message_dit_quoi_faire(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "EMBEDDED", tmp_path / "vide")
    monkeypatch.setattr(tr, "CHROMIUM_CANDIDATES", ())
    with pytest.raises(tr.RenderUnavailable) as e:
        tr.browser_path()
    assert "fetch-engine" in str(e.value)


def test_la_plateforme_donne_un_nom_de_paquet_connu():
    assert tr._platform() in ("linux64", "win64", "mac-x64", "mac-arm64")


def test_les_noms_attendus_couvrent_les_trois_systemes():
    noms = " ".join(tr.EMBEDDED_NAMES)
    assert "linux64" in noms and "win64" in noms and "mac-arm64" in noms
