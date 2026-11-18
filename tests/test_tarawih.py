"""Tarawih: nightly, bounded by a period, after isha; heard by default, filmed only when confirmed."""

import datetime as dt

import pytest

from sada import events as ev
from sada import runs
from sada.scenes import SCENES
from sada.state import PICKABLE_SCENES, Machine, MachineParams, Signals, State

TIMES = {"isha": "19:30"}
RAW = {
    "uid": "tarawih",
    "kind": "tarawih",
    "title": "Tarawih",
    "speaker": "Imam Exemple",
    "mode": "audio",
    "period": {"from": "2027-02-08", "until": "2027-03-09"},
    "recurrence": {"after": "isha", "offset_min": 20},
    "max_min": 90,
}


def test_une_nuit_dans_la_periode_apres_l_isha():
    e = ev.parse(RAW)
    inst = ev.instances_on([e], dt.date(2027, 2, 20), TIMES)
    assert len(inst) == 1
    assert inst[0].starts == dt.datetime(2027, 2, 20, 19, 50)  # isha + 20 min: isha itself is not covered
    assert inst[0].as_event()["mode"] == "audio" and inst[0].as_event()["max_min"] == 90


def test_rien_hors_de_la_periode():
    e = ev.parse(RAW)
    for day in (dt.date(2027, 2, 7), dt.date(2027, 3, 10), dt.date(2027, 6, 1)):
        assert ev.instances_on([e], day, TIMES) == []
    assert ev.instances_on([e], dt.date(2027, 3, 9), TIMES)  # the last night is included


@pytest.mark.parametrize(
    "patch",
    [
        {"period": None},
        {"period": {"from": "2027-03-09", "until": "2027-02-08"}},
        {"recurrence": {"after": "isha", "offset_min": 0}},
        {"mode": "always"},
    ],
)
def test_entree_tarawih_mal_formee_refusee(patch):
    with pytest.raises(ValueError):
        ev.parse({**RAW, **patch})


def test_toujours_apres_l_isha_meme_si_l_entree_dit_autre_chose():
    e = ev.parse({**RAW, "recurrence": {"after": "dhuhr", "offset_min": 15, "time": "03:00"}})
    assert e.recurrence.after == "isha"


def _machine(mode="audio", max_min=90):
    meta = [{"kind": "tarawih", "title": "Tarawih", "speaker": "Imam Exemple", "mode": mode, "max_min": max_min}]
    return Machine([dt.datetime(2027, 2, 20, 19, 50)], MachineParams(), extras=meta)


class Drv:
    def __init__(self, m, now):
        self.m, self.now, self.t = m, now, 1000.0
        self.voice = self.silence = 0.0

    def run(self, n, voice=False):
        for _ in range(n):
            self.t += 1
            self.now += dt.timedelta(seconds=1)
            self.voice, self.silence = (self.voice + 1, 0.0) if voice else (0.0, self.silence + 1)
            self.m.tick(Signals(self.now, self.t, self.voice, self.silence, None))


def _start(mode="audio", max_min=90):
    m = _machine(mode, max_min)
    d = Drv(m, dt.datetime(2027, 2, 20, 19, 48))
    d.run(5)
    d.run(200, voice=True)
    assert m.state == State.TARAWIH
    return m, d


def test_mode_audio_son_ouvert_camera_masquee():
    m, _ = _start("audio")
    assert m.scene == "tarawih"
    assert SCENES["tarawih"].full is True and SCENES["tarawih"].mic is True


def test_mode_video_camera_et_son():
    m, _ = _start("video")
    assert m.scene == "tarawih_video"
    assert SCENES["tarawih_video"].full is False and SCENES["tarawih_video"].mic is True


def test_fin_sur_silence_prolonge_sans_ecran_de_fin():
    m, d = _start()
    d.run(241)  # 4 min of silence, then the grace
    d.run(61)
    assert m.state == State.STOPPED
    assert m.records[0].end_reason == "silence prolongé"


def test_duree_maximale_choisie_par_la_mosquee():
    m, d = _start(max_min=15)
    d.run(15 * 60, voice=True)
    assert m.state == State.STOPPED
    assert "durée maximale" in (m.records[0].end_reason or "")


def test_pas_de_clips_pour_les_tarawih():
    m, _ = _start()
    s = m.sessions_dicts()[0]
    assert s["kind"] == "tarawih" and not runs.produces_content(s)
    assert runs.produces_content({"status": "done", "kind": "dars"})


def test_une_priere_ne_se_force_pas_a_la_main():
    assert "tarawih" not in PICKABLE_SCENES and "tarawih_video" not in PICKABLE_SCENES


def test_l_ecran_audio_se_dessine(tmp_path):
    from sada.config import Config
    from sada.scenes import Moment, SceneRenderer

    r = SceneRenderer(Config())
    img = r.content("tarawih", Moment(dt.datetime(2027, 2, 20, 20, 0), event={"kind": "tarawih", "speaker": "Imam"}))
    assert img.size == (1280, 720) and img.getpixel((5, 5))[3] == 255  # opaque: the camera is masked
    bandeau = r.content("tarawih_video", Moment(dt.datetime(2027, 2, 20, 20, 0), event=RAW))
    assert bandeau.getpixel((5, 5))[3] == 0  # the camera stays visible
