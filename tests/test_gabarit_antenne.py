"""Le gabarit importé à l'antenne, avec un rendu factice : ce qui compte ici est la décision.

Un faux rendu (aucun navigateur) permet de vérifier ce qui est vraiment fragile : un écran qui ne
change pas n'est pas redessiné, l'entracte l'est une fois par seconde, un échec répété ramène au
dessin du moteur, et une image n'est jamais réécrite sans raison.
"""
from __future__ import annotations

import time

import pytest
from PIL import Image

from sada.gabarit_antenne import ECRANS, AntenneGabarit


class FauxRendu:
    """Tient le compte des rendus ; rend une image 1280×720 d'une couleur propre à l'écran."""

    instances: list = []

    def __init__(self, cfg):
        self.appels: list[str] = []
        self.demarre = False
        self.ferme = False
        self.echoue = False
        FauxRendu.instances.append(self)

    def start(self):
        self.demarre = True

    def close(self):
        self.ferme = True

    def image(self, page, variables):
        self.appels.append(page)
        if self.echoue:
            raise RuntimeError("navigateur tombé")
        import io

        index = [PAGES[e] for e in ECRANS].index(page) if page in PAGES.values() else 0
        couleur = tuple((index * 40 + 20,) * 3)
        out = io.BytesIO()
        Image.new("RGB", (1280, 720), couleur).save(out, "PNG")
        return out.getvalue()


PAGES = {e: f"<html><!-- écran {e} --></html>" for e in ECRANS}


def antenne(monkeypatch, **kw):
    antenne = AntenneGabarit(object(), FauxRendu, **kw)
    antenne.page = PAGES
    return antenne


def test_sans_fichier_importe_le_dessin_du_moteur_reste(monkeypatch):
    appels: list = []
    a = AntenneGabarit(object(), FauxRendu, fetch=lambda url, token: {"present": False, "pages": {}},
                       log_fn=appels.append)
    assert a.charger("http://bo", 0) is False
    assert a.actif is False
    assert a.image("avant") is None
    assert any("aucun fichier importé" in m for m in appels)


def test_le_fichier_importe_est_bien_lu_et_signale(monkeypatch):
    appels: list = []
    reponse = {"present": True, "nom": "Mosquée d'essai", "pages": PAGES, "variables": {"arabe_khutba": "خطبة"}}
    a = AntenneGabarit(object(), FauxRendu, fetch=lambda url, token: reponse, log_fn=appels.append)
    assert a.charger("http://bo", 0) is True
    assert a.actif is True
    assert a._variables["arabe_khutba"] == "خطبة"
    assert any("ce fichier sera affiché à l'antenne" in m for m in appels)


def test_un_back_office_muet_n_empeche_pas_le_direct():
    def tombe(url, token):
        raise OSError("connexion refusée")

    appels: list = []
    a = AntenneGabarit(object(), FauxRendu, fetch=tombe, log_fn=appels.append)
    assert a.charger("http://bo", 0) is False
    assert a.image("avant") is None
    assert any("injoignable" in m for m in appels)


def test_un_ecran_qui_ne_change_pas_n_est_pas_redessine(monkeypatch):
    a = antenne(monkeypatch)
    a.image("avant")
    a.image("avant")
    a.image("avant")
    assert len(FauxRendu.instances[-1].appels) == 1


def test_l_entracte_se_redessine_une_fois_par_seconde(monkeypatch):
    a = antenne(monkeypatch)
    a.image("entracte")
    valeurs = {"entracte": iter(["12:00:00", "12:00:00", "12:00:01", "12:00:01"])}

    def fausse_heure():
        return next(valeurs["entracte"])

    monkeypatch.setattr("sada.gabarit_antenne.time.strftime", lambda _f: fausse_heure())
    a._dernieres.clear()
    a.image("entracte")
    a.image("entracte")
    a.image("entracte")
    a.image("entracte")
    assert len(FauxRendu.instances[-1].appels) == 3


def test_chaque_ecran_donne_une_image_differente(monkeypatch):
    a = antenne(monkeypatch)
    images = {e: a.image(e) for e in ECRANS}
    assert all(images.values())
    assert len(set(images.values())) == len(ECRANS)


def test_trois_echecs_de_suite_ramenent_au_dessin_du_moteur(monkeypatch):
    appels: list = []
    a = antenne(monkeypatch, log_fn=appels.append)
    a.image("avant")
    FauxRendu.instances[-1].echoue = True
    assert a.image("priere") is None
    assert a.image("priere") is None
    assert a.image("priere") is None
    assert a.actif is False
    assert any("trois échecs" in m for m in appels)


def test_la_fermeture_ferme_le_navigateur_de_rendu(monkeypatch):
    a = antenne(monkeypatch)
    a.image("avant")
    rendu = FauxRendu.instances[-1]
    a.fermer()
    assert rendu.ferme is True
    assert a._rendu is None


def test_l_instant_de_l_ecran_est_stable_hors_entracte(monkeypatch):
    a = antenne(monkeypatch)
    assert a.variable_de_l_instant("avant") == ""
    avant = a.variable_de_l_instant("entracte")
    assert len(avant) == 8 and avant.count(":") == 2
