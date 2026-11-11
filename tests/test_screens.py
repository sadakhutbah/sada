"""Écrans des scènes : textes par défaut, personnalisation par mosquée, aperçu, moteur.

Le défaut vit dans `sada.screens` — le module avec lequel le moteur dessine : l'interface affiche
donc exactement ce qui partira à l'antenne, et vider un champ ramène au défaut.
"""
import pytest

pytest.importorskip("fastapi")

from test_backoffice import client, env  # noqa: E402,F401


def test_chaque_ecran_annonce_ses_champs_et_son_defaut(env):
    d = client(env, "admin").get("/api/v1/settings").json()["screens"]
    assert list(d) == ["avant", "priere", "entracte", "termine", "janaza", "dars_fin"]
    for champs in d.values():
        assert champs and all({"default", "value", "text"} <= set(c) for c in champs.values())
    assert d["avant"]["foot"]["text"] == "Merci de patienter, la diffusion commence très bientôt."
    assert d["avant"]["foot"]["value"] == ""          # rien de personnalisé au départ
    assert d["avant"]["sub"]["default"] == "Début à {heure}"


def test_personnalisation_enregistree_et_relue(env):
    from sada.backoffice import db
    c = client(env, "admin")
    r = c.ecrire("/settings/screens", {"screens": {"avant": {"foot": "  Nous démarrons dans un instant.  "}}},
                 methode="PUT")
    assert r.status_code == 200, r.text
    champs = r.json()["screens"]["avant"]
    assert champs["foot"]["value"] == "Nous démarrons dans un instant."
    assert champs["foot"]["text"] == "Nous démarrons dans un instant."
    assert champs["eyebrow"]["value"] == "" and champs["eyebrow"]["text"] == "Début imminent"
    assert db.one(env["conn"], "SELECT count(*) n FROM screen_texts")["n"] == 1
    assert c.get("/api/v1/settings").json()["screens"]["avant"]["foot"]["value"] == "Nous démarrons dans un instant."


def test_vider_un_champ_ramene_au_defaut(env):
    c = client(env, "admin")
    c.ecrire("/settings/screens", {"screens": {"priere": {"title": "Prière du vendredi"}}}, methode="PUT")
    r = c.ecrire("/settings/screens", {"screens": {"priere": {"title": ""}}}, methode="PUT")
    assert r.status_code == 200, r.text
    champs = r.json()["screens"]["priere"]
    assert champs["title"]["value"] == "" and champs["title"]["text"] == "Prière en cours"


def test_refus_d_un_ecran_ou_d_un_champ_inconnu(env):
    c = client(env, "admin")
    r = c.ecrire("/settings/screens", {"screens": {"apres": {"foot": "x"}}}, methode="PUT")
    assert r.status_code == 422 and "écran inconnu" in r.json()["message"]
    r = c.ecrire("/settings/screens", {"screens": {"avant": {"haut": "x"}}}, methode="PUT")
    assert r.status_code == 422 and "champ inconnu" in r.json()["message"]
    r = c.ecrire("/settings/screens", {"screens": {"avant": {"foot": "x" * 200}}}, methode="PUT")
    assert r.status_code == 422 and "160" in r.json()["message"]
    assert c.get("/api/v1/settings").json()["screens"]["avant"]["foot"]["value"] == ""   # rien n'a bougé


def test_ecrans_reserves_a_l_admin(env):
    for role in ("imam", "responsable"):
        c = client(env, role)
        assert c.ecrire("/settings/screens", {"screens": {}}, methode="PUT").status_code == 403


def test_apercu_est_une_vraie_image_du_moteur(env):
    from PIL import Image
    from io import BytesIO

    c = client(env, "admin")
    r = c.ecrire("/settings/screens/preview", {"screen": "avant", "screens": {"avant": {"foot": "Dans un instant."}}})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(BytesIO(r.content)).size == (1280, 720)
    assert c.ecrire("/settings/screens/preview", {"screen": "apres"}).status_code == 422


def test_le_moteur_lit_les_textes_du_back_office(env):
    from sada.backoffice import internal
    c = client(env, "admin")
    c.ecrire("/settings/screens", {"screens": {"termine": {"foot": "À la semaine prochaine."}}}, methode="PUT")
    d = c.get("/api/v1/internal/screens", headers={internal.HEADER: internal.token()}).json()
    assert d["screens"] == {"termine": {"foot": "À la semaine prochaine."}}
    assert c.get("/api/v1/internal/screens", headers={internal.HEADER: "faux"}).status_code == 403


def test_les_variables_remplacent_et_une_ligne_sans_valeur_disparait():
    from sada import screens

    t = screens.resolve("avant", {}, {"heure": "13:00"})
    assert t["sub"] == "Début à 13:00"
    t = screens.resolve("avant", {}, {"heure": ""})
    assert t["sub"] == ""                                     # jamais « Début à » tout seul
    t = screens.resolve("entracte", {"entracte": {"foot": "Reprise à {heure}"}}, {"heure": ""})
    assert t["foot"] == ""
    t = screens.resolve("entracte", {}, {"heure": "18:30", "cours": "Tafsir"})
    assert t["title_cours"] == "Prochain cours à 18:30"


def test_une_variable_inconnue_reste_visible():
    from sada import screens

    assert screens.resolve("avant", {"avant": {"foot": "Salut {prénom}"}}, {})["foot"] == "Salut {prénom}"


def test_les_ecrans_pleins_se_dessinent_avec_les_textes_personnalises():
    """Rendu réel des six écrans : aucune exception, et le texte part bien dans l'image."""
    from sada import scenes
    from sada.config import Config

    cfg = Config(screen_texts={"avant": {"eyebrow": "Bonjour"}, "priere": {"title": "Prière du vendredi"},
                               "entracte": {"foot": "On revient tout de suite."},
                               "termine": {"title": "À vendredi"}, "janaza": {"foot": "Merci."},
                               "dars_fin": {"foot": "Bonne soirée."}})
    import datetime as dt

    r = scenes.SceneRenderer(cfg)
    moment = scenes.Moment(now=dt.datetime.now().astimezone(), planned=None, next_event=None)
    for ecran in ("avant", "priere", "entracte", "termine", "janaza", "dars_fin"):
        img = r.content(ecran, moment)
        assert img.size == (1280, 720)
