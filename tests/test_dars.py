"""Dars et conférences : entrée dédiée, fin souple, écran de fin, aucun mélange avec la khutba."""
import datetime as dt

from minbar.state import Machine, MachineParams, SCENE_OF, Signals, State

D = dt.date(2026, 11, 10)                    # mardi


def at(h, m, s=0):
    return dt.datetime.combine(D, dt.time(h, m, s))


class Drv:
    """Fait avancer l'horloge et les signaux de la machine, sans E/S."""

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
    m = Machine([at(19, 0)], MachineParams(**params), log=logs.append,
                kinds=["dars"], meta=meta)
    return m, logs


def test_dars_demarre_en_scene_dars():
    m, _ = mk()
    d = Drv(m, at(18, 55))
    d.run(2)
    assert m.state == State.ARME
    d.run(20, voice=True)                   # la voix arme le stream, la scène ne bascule qu'à l'heure
    assert m.state == State.AVANT
    d.run(300, voice=True)                  # 19:00 passée : entrée en dars
    assert m.state == State.DARS
    assert m.scene == "dars"
    assert SCENE_OF[State.DARS] == "dars"
    assert m.rec.kind == "dars" and m.rec.title == "Tafsir Al-Kahf"
    assert m.status()["kind"] == "dars"
    assert m.status()["event"]["session_no"] == 7


def test_fin_par_silence_prolonge_puis_ecran_de_fin():
    m, _ = mk()
    d = Drv(m, at(18, 59))
    d.run(5)
    d.run(75, voice=True)
    assert m.state == State.DARS
    d.run(181, voice=False)                 # seuil de silence d'un dars : 3 min
    assert m.state == State.DARS            # le délai de grâce commence
    d.run(91, voice=False)                  # grâce de 90 s dépassée
    assert m.state == State.DARS_FIN
    d.run(46)
    assert m.state == State.FIN


def test_garde_fou_duree_maximale_dars():
    m, _ = mk(dars_max_s=300.0)
    d = Drv(m, at(18, 59))
    d.run(5)
    d.run(75, voice=True)
    assert m.state == State.DARS
    d.run(320, voice=True)                  # bien au-delà du maximum, sans silence
    assert m.state == State.DARS_FIN
    assert m.rec.status == "done"
    assert "durée maximale" in (m.rec.end_reason or "")


def test_une_khutba_reste_une_khutba():
    m = Machine([at(19, 0)], MachineParams(), kinds=["khutba"], meta=[{}])
    d = Drv(m, at(18, 59))
    d.run(5)
    d.run(75, voice=True)
    assert m.state == State.KHUTBA and m.scene == "khutba"
    assert m.rec.kind == "khutba"


def test_fenetre_plus_large_pour_un_dars():
    m, _ = mk()
    d = Drv(m, at(20, 20))                  # T+80 min : hors fenêtre d'une khutba (45), dans celle d'un dars (90)
    d.run(25, voice=True)
    assert m.state != State.FIN
    assert m.state == State.DARS


def test_commande_start_en_dars():
    m, _ = mk()
    d = Drv(m, at(19, 0))
    d.step()
    m.command("start", d.sig())
    assert m.state == State.DARS and m.scene == "dars"
