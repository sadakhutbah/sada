"""Tarawih: nightly, bounded by a period, armed after isha; heard by default, filmed only when confirmed.
Starts and ends on signals (voice, silence, presence), several reciters switched by the control room."""

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
    # no iqama published: adhan + 10 min, plus 10 min of prayer: the end of isha, not the tarawih
    assert inst[0].starts == dt.datetime(2027, 2, 20, 19, 50)
    out = inst[0].as_event()
    assert out["mode"] == "audio" and out["max_min"] == 90 and out["offset_min"] == 20
    assert out["night"] == 13  # 8 February is night 1


def test_fin_de_l_isha_depuis_l_iqama_mawaqit():
    e = ev.parse({**RAW, "isha_min": 12})
    inst = ev.instances_on([e], dt.date(2027, 2, 20), {"isha": "19:30", "isha_iqama": "19:45"})
    assert inst[0].starts == dt.datetime(2027, 2, 20, 19, 57)


def test_iqama_lue_dans_la_configuration_mawaqit():
    from sada import mawaqit

    conf = {
        "times": ["06:00", "13:00", "16:00", "18:30", "19:30"],
        "iqamaCalendar": [{"20": ["+20"] * 4 + ["+15"]}] * 12,
    }
    assert mawaqit.iqama_times(conf, dt.date(2027, 2, 20))[4] == "19:45"
    assert mawaqit.iqama_times({**conf, "fixedIqama": ["", "", "", "", "20:00"]}, dt.date(2027, 2, 20))[4] == "20:00"
    assert mawaqit.iqama_times({"times": conf["times"]}, dt.date(2027, 2, 20)) is None  # nothing invented


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
        {"recurrence": {"after": "isha", "offset_min": -5}},
        {"mode": "always"},
    ],
)
def test_entree_tarawih_mal_formee_refusee(patch):
    with pytest.raises(ValueError):
        ev.parse({**RAW, **patch})


def test_toujours_apres_l_isha_meme_si_l_entree_dit_autre_chose():
    e = ev.parse({**RAW, "recurrence": {"after": "dhuhr", "offset_min": 15, "time": "03:00"}})
    assert e.recurrence.after == "isha"


ISHA_END = dt.datetime(2027, 2, 20, 19, 50)


def _machine(mode="audio", max_min=180, **extra):
    meta = [{"kind": "tarawih", "title": "Tarawih", "mode": mode, "max_min": max_min, "offset_min": 20, **extra}]
    return Machine([ISHA_END], MachineParams(), extras=meta, pos_fn=lambda: (0, 0.0))


class Drv:
    def __init__(self, m, now):
        self.m, self.now, self.t = m, now, 1000.0
        self.voice = self.silence = 0.0

    def run(self, n, voice=False, presence=None):
        for _ in range(int(n)):
            self.t += 1
            self.now += dt.timedelta(seconds=1)
            self.voice, self.silence = (self.voice + 1, 0.0) if voice else (0.0, self.silence + 1)
            self.m.tick(Signals(self.now, self.t, self.voice, self.silence, presence))

    def until(self, when: dt.datetime, voice=False):
        self.run((when - self.now).total_seconds(), voice=voice)


def _start(mode="audio", max_min=180, **extra):
    """A night that started on the tarawih proper (end of isha + 20 min) and ran 30 min of recitation."""
    m = _machine(mode, max_min, **extra)
    d = Drv(m, dt.datetime(2027, 2, 20, 20, 9))
    d.run(60)
    d.run(30 * 60, voice=True)
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


def test_l_isha_n_est_jamais_diffusee():
    m = _machine(after_isha=True)
    d = Drv(m, dt.datetime(2027, 2, 20, 19, 30))
    d.until(ISHA_END - dt.timedelta(seconds=1), voice=True)  # the imam leads isha: voice throughout
    assert m.state == State.IDLE and not m.stream_on


def test_appel_aux_dons_diffuse_seulement_avec_l_option():
    on = _machine(after_isha=True)
    d = Drv(on, ISHA_END)
    d.run(60, voice=True)  # announcement right after isha
    assert on.state == State.TARAWIH and "après l'isha" in on.last_reason

    off = _machine(after_isha=False)
    d = Drv(off, ISHA_END)
    d.run(10 * 60, voice=True)  # same appeal: not on air
    assert off.state == State.ARMED and not off.stream_on
    d.until(ISHA_END + dt.timedelta(minutes=20))
    d.run(30, voice=True)  # the tarawih proper
    assert off.state == State.TARAWIH


def test_pause_entre_deux_series_ne_termine_pas():
    m, d = _start()
    d.run(4 * 60)  # people seated, a few minutes of silence
    d.run(60, voice=True)
    assert m.state == State.TARAWIH and m.grace_until is None


def test_silence_mais_quelqu_un_devant_ne_termine_pas():
    m, d = _start()
    d.run(9 * 60, presence=True)
    assert m.state == State.TARAWIH


def test_silence_prolonge_et_personne_termine():
    m, d = _start()
    d.run(6 * 60 + 91, presence=False)
    assert m.state == State.STOPPED
    assert m.records[0].end_reason == "silence prolongé"


def test_tres_long_silence_termine_meme_avec_du_mouvement():
    m, d = _start()
    d.run(12 * 60 + 91, presence=True)
    assert m.state == State.STOPPED


def test_retour_de_la_voix_pendant_la_grace_annule():
    m, d = _start()
    d.run(6 * 60 + 30)
    assert m.grace_until is not None
    d.run(5, voice=True)
    assert m.state == State.TARAWIH and m.grace_until is None


def test_pas_de_fin_avant_la_duree_minimale():
    m = _machine()
    d = Drv(m, dt.datetime(2027, 2, 20, 20, 9))
    d.run(60)
    d.run(60, voice=True)
    d.run(10 * 60)
    assert m.state == State.TARAWIH


def test_plafond_de_securite():
    m, d = _start(max_min=40)
    d.run(11 * 60, voice=True)  # uninterrupted recitation: only the cap ends it
    assert m.state == State.STOPPED
    assert "sécurité" in (m.records[0].end_reason or "")


def test_demarrage_et_arret_manuels():
    m = _machine()
    d = Drv(m, dt.datetime(2027, 2, 20, 19, 40))
    d.run(5)
    m.command("tarawih", Signals(d.now, d.t))
    assert m.state == State.TARAWIH and not m.override  # the end stays automatic
    m.command("tarawih", Signals(d.now, d.t))  # already on air: no effect
    assert m.state == State.TARAWIH and m.records[0].status == "live"
    m.command("stop", Signals(d.now, d.t))
    assert m.state != State.TARAWIH and m.records[0].status == "done"


def test_demarrage_manuel_refuse_sans_tarawih_ce_soir():
    m = Machine([ISHA_END], MachineParams(), extras=[{"kind": "dars", "title": "Cours"}])
    m.command("tarawih", Signals(ISHA_END, 0.0))
    assert m.state != State.TARAWIH and m.records[0].status == "pending" and m.live is None


def test_recitant_suivant_et_chapitres():
    pos = [0, 100.0]
    m = Machine(
        [ISHA_END],
        MachineParams(),
        extras=[{"kind": "tarawih", "mode": "audio", "offset_min": 20, "reciters": ["Imam A", "Imam B"]}],
        pos_fn=lambda: tuple(pos),
    )
    d = Drv(m, dt.datetime(2027, 2, 20, 20, 9))
    d.run(60)
    d.run(30, voice=True)
    assert m.state == State.TARAWIH
    assert m.rec.event()["reciter"] is None  # two reciters: nobody is guessed
    pos[1] = 160.0
    m.command("next_reciter", Signals(d.now, d.t))
    assert m.rec.event()["reciter"] == "Imam A"
    pos[1] = 1300.0
    m.command("next_reciter", Signals(d.now, d.t))
    assert m.live.reciter_marks == [{"at": 60.0, "name": "Imam A"}, {"at": 1200.0, "name": "Imam B"}]


def test_un_seul_recitant_est_connu():
    m, _ = _start(reciters=["Imam A"])
    assert m.rec.event()["reciter"] == "Imam A"


def test_recitant_suivant_refuse_hors_tarawih():
    m = _machine(reciters=["Imam A"])
    m.command("next_reciter", Signals(ISHA_END, 0.0))
    assert m.state == State.IDLE and m.records[0].reciter_marks == []


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
    img = r.content("tarawih", Moment(dt.datetime(2027, 2, 20, 20, 0), event={"kind": "tarawih", "reciter": "Imam"}))
    assert img.size == (1280, 720) and img.getpixel((5, 5))[3] == 255  # opaque: the camera is masked
    bandeau = r.content("tarawih_video", Moment(dt.datetime(2027, 2, 20, 20, 0), event=RAW))
    assert bandeau.getpixel((5, 5))[3] == 0  # the camera stays visible
