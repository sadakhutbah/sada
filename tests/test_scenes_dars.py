"""Écrans dars : bandeau, écran de fin, entracte qui annonce un cours."""
import datetime as dt

from PIL import ImageChops

from minbar.config import Config
from minbar.events import EventInstance
from minbar.scenes import SCENE_LABELS, SCENES, Moment, SceneRenderer
from minbar.state import SCENE_OF

NOW = dt.datetime(2026, 11, 10, 19, 10)
EV = {"kind": "dars", "title": "Tafsir de la sourate Al-Kahf", "speaker": "Imam Abdallah",
      "series": "Tafsir Al-Kahf", "session_no": 7}
NEXT = EventInstance(starts=dt.datetime(2026, 11, 17, 19, 0), kind="dars",
                     title="Tafsir de la sourate Al-Kahf", session=8)


def test_dars_bandeau_et_ecran_de_fin():
    r = SceneRenderer(Config())
    bandeau = r.content("dars", Moment(NOW, event=EV))
    assert bandeau.size == (1280, 720) and bandeau.mode == "RGBA"
    assert bandeau.getchannel("A").getextrema()[1] > 0            # du contenu est dessiné
    assert bandeau.crop((0, 0, 1280, 400)).getchannel("A").getextrema()[1] == 0   # bas de l'image
    fin = r.content("dars_fin", Moment(NOW, next_event=NEXT))
    assert fin.size == (1280, 720)
    assert ImageChops.difference(bandeau, fin).getbbox() is not None


def test_scenes_dars_declarees():
    assert SCENES["dars"].full is False and SCENES["dars"].mic is True
    assert SCENES["dars_fin"].full is True and SCENES["dars_fin"].mic is False


def test_les_tables_de_scenes_s_accordent():
    """SCENES, SCENE_OF et les libellés sont trois registres tenus à la main : un nom oublié casse une page."""
    assert set(SCENES) == {v for v in SCENE_OF.values() if v} == set(SCENE_LABELS)
    assert all(SCENES[n].label == SCENE_LABELS[n] and SCENE_LABELS[n] for n in SCENES)


def test_calque_de_dars_se_pose_sur_la_camera():
    """Bandeau partiel (la caméra reste visible) ; l'écran de fin est un écran plein."""
    r = SceneRenderer(Config())
    bandeau = r.content("dars", Moment(NOW, event=EV))
    assert bandeau.getpixel((5, 5))[3] == 0                       # coin transparent
    plein = r.content("dars_fin", Moment(NOW, next_event=NEXT))
    assert plein.crop((0, 0, 1280, 360)).getchannel("A").getextrema()[1] > 0   # bloc de textes présent


def test_entracte_annonce_un_prochain_cours():
    r = SceneRenderer(Config())
    cours = r.content("entracte", Moment(NOW, next_event=NEXT))
    autre = r.content("entracte", Moment(NOW))
    assert ImageChops.difference(cours, autre).getbbox() is not None


def test_dars_sans_evenement_ne_casse_rien():
    r = SceneRenderer(Config())
    assert r.content("dars", Moment(NOW)).size == (1280, 720)
    assert r.content("dars_fin", Moment(NOW)).size == (1280, 720)
