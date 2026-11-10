"""Dars and conferences: dedicated entry, soft ending, end screen, no mixing with the khutba."""
import datetime as dt

import pytest

from minbar.state import Machine, MachineParams, Signals, State

D = dt.date(2026, 11, 10)                    # Tuesday


def at(h, m, s=0):
    return dt.datetime.combine(D, dt.time(h, m, s))


class Drv:
    """Advances the clock and the machine signals, without I/O."""

    def __init__(self, m, now):
        self.m, self.now, self.t = m, now, 1000.0
        self.voice = self.silence = 0.0

    def step(self, dsec=1.0, voice=False):
        self.t += dsec
        self.now += dt.timedelta(seconds=dsec)
        if voice:
            self.voice += dsec
            self.silence = 0
        else:
            self.silence += dsec
            self.voice = 0
        self.m.tick(Signals(self.now, self.t, self.voice, self.silence, None))

    def run(self, n, voice=False):
        for _ in range(n):
            self.step(voice=voice)

    def sig(self):
        return Signals(self.now, self.t, self.voice, self.silence, None)


def mk(**params):
    logs = []
    meta = [{"kind": "dars", "title": "Tafsir Al-Kahf", "speaker": "Imam Abdallah",
             "series": "Tafsir Al-Kahf", "session_no": 7}]
    m = Machine([at(19, 0)], MachineParams(**params), log=logs.append, extras=meta)
    return m, logs


def test_dars_demarre_en_scene_dars():
    m, _ = mk()
    d = Drv(m, at(18, 55))
    d.run(2)
    assert m.state == State.ARMED
    d.run(20, voice=True)                   # voice arms the stream, the scene only switches at the scheduled time
    assert m.state == State.BEFORE
    d.run(300, voice=True)                  # 19:00 passed: entering dars
    assert m.state == State.DARS
    assert m.scene == "dars"
    assert m.rec.kind == "dars" and m.rec.title == "Tafsir Al-Kahf"
    assert m.status()["kind"] == "dars"
    assert m.status()["event"]["session_no"] == 7


def test_fin_par_silence_prolonge_puis_ecran_de_fin():
    m, _ = mk()
    d = Drv(m, at(18, 59))
    d.run(5)
    d.run(75, voice=True)
    assert m.state == State.DARS
    d.run(181, voice=False)                 # silence threshold for a dars: 3 min
    assert m.state == State.DARS            # the grace delay starts
    d.run(91, voice=False)                  # 90 s grace exceeded
    assert m.state == State.DARS_END
    d.run(46)
    assert m.state == State.STOPPED


def test_garde_fou_duree_maximale_dars():
    m, _ = mk(dars_max_s=300.0)
    d = Drv(m, at(18, 59))
    d.run(5)
    d.run(75, voice=True)
    assert m.state == State.DARS
    d.run(320, voice=True)                  # well beyond the maximum, without silence
    assert m.state == State.DARS_END
    assert m.rec.status == "done"
    assert "durée maximale" in (m.rec.end_reason or "")


def test_une_khutba_reste_une_khutba():
    m = Machine([at(19, 0)], MachineParams(), extras=[{"kind": "khutba"}])
    d = Drv(m, at(18, 59))
    d.run(5)
    d.run(75, voice=True)
    assert m.state == State.KHUTBA and m.scene == "khutba"
    assert m.rec.kind == "khutba"


def test_fenetre_plus_large_pour_un_dars():
    m, _ = mk()
    d = Drv(m, at(20, 20))                  # T+80 min: outside a khutba window (45), inside a dars window (90)
    d.run(25, voice=True)
    assert m.state != State.STOPPED
    assert m.state == State.DARS


def test_commande_start_en_dars():
    m, _ = mk()
    d = Drv(m, at(19, 0))
    d.step()
    m.command("start", d.sig())
    assert m.state == State.DARS and m.scene == "dars"


def test_extras_et_sessions_doivent_s_aligner():
    """A silent misalignment would turn a dars into a khutba: the machine refuses."""
    with pytest.raises(ValueError):
        Machine([at(19, 0), at(20, 0)], MachineParams(),
                extras=[{"kind": "dars"}])
