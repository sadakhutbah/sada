"""Dars screens: banner, end screen, interval that announces a class."""

import datetime as dt

from PIL import ImageChops

from sada.config import Config
from sada.events import EventInstance
from sada.scenes import SCENES, Moment, SceneRenderer
from sada.state import SCENE_OF

NOW = dt.datetime(2026, 11, 10, 19, 10)
EV = {
    "kind": "dars",
    "title": "Tafsir de la sourate Al-Kahf",
    "speaker": "Imam Abdallah",
    "series": "Tafsir Al-Kahf",
    "session_no": 7,
}
NEXT = EventInstance(
    starts=dt.datetime(2026, 11, 17, 19, 0), kind="dars", title="Tafsir de la sourate Al-Kahf", session=8
)


def test_dars_bandeau_et_ecran_de_fin():
    r = SceneRenderer(Config())
    bandeau = r.content("dars", Moment(NOW, event=EV))
    assert bandeau.size == (1280, 720) and bandeau.mode == "RGBA"
    assert bandeau.getchannel("A").getextrema()[1] > 0  # some content is drawn
    assert bandeau.crop((0, 0, 1280, 400)).getchannel("A").getextrema()[1] == 0  # bottom of the image
    fin = r.content("dars_fin", Moment(NOW, next_event=NEXT))
    assert fin.size == (1280, 720)
    assert ImageChops.difference(bandeau, fin).getbbox() is not None


def test_scenes_dars_declarees():
    assert SCENES["dars"].full is False and SCENES["dars"].mic is True
    assert SCENES["dars_fin"].full is True and SCENES["dars_fin"].mic is False


def test_les_tables_de_scenes_s_accordent():
    """A scene referenced by the state machine but missing from the renderer breaks the live."""
    assert set(SCENES) == {v for v in SCENE_OF.values() if v}


def test_calque_de_dars_se_pose_sur_la_camera():
    """Partial banner (the camera stays visible); the end screen is a full screen."""
    r = SceneRenderer(Config())
    bandeau = r.content("dars", Moment(NOW, event=EV))
    assert bandeau.getpixel((5, 5))[3] == 0  # transparent corner
    plein = r.content("dars_fin", Moment(NOW, next_event=NEXT))
    assert plein.crop((0, 0, 1280, 360)).getchannel("A").getextrema()[1] > 0  # text block present


def test_entracte_annonce_un_prochain_cours():
    r = SceneRenderer(Config())
    cours = r.content("entracte", Moment(NOW, next_event=NEXT))
    autre = r.content("entracte", Moment(NOW))
    assert ImageChops.difference(cours.convert("RGB"), autre.convert("RGB")).getbbox() is not None


def test_compte_a_rebours_lisible_quelle_que_soit_l_echeance():
    """Un « 4694:43 » ne veut rien dire : au-delà d'une heure, on compte en heures puis en jours."""
    from sada.scenes import _countdown

    assert _countdown(43) == "00:43"
    assert _countdown(754) == "12:34"
    assert _countdown(3 * 3600 + 5 * 60) == "3 h 05"
    assert _countdown(3 * 86400 + 6 * 3600 + 43 * 60 + 17) == "3 j 06 h"


def test_dars_sans_evenement_ne_casse_rien():
    r = SceneRenderer(Config())
    assert r.content("dars", Moment(NOW)).size == (1280, 720)
    assert r.content("dars_fin", Moment(NOW)).size == (1280, 720)
