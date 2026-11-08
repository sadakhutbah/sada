import datetime as dt
from minbar import runs
from minbar.state import Machine, MachineParams, Signals, State

D = dt.date(2026, 11, 6)


def at(h, m, s=0):
    return dt.datetime.combine(D, dt.time(h, m, s))


def mk(times=("12:30", "13:30"), **kw):
    sess = [at(int(t[:2]), int(t[3:])) for t in times]
    logs = []
    p = MachineParams(**kw)
    return Machine(sess, p, log=logs.append), logs


class Drv:
    """Pilote de test : avance t (temps réel) et now (horloge mosquée) ensemble."""
    def __init__(self, m, now):
        self.m, self.now, self.t = m, now, 1000.0
        self.voice = 0.0
        self.silence = 0.0

    def step(self, dsec=1.0, voice=None, clock=None):
        self.t += dsec
        self.now += dt.timedelta(seconds=dsec if clock is None else clock)
        if voice is True:
            self.voice += dsec; self.silence = 0
        elif voice is False:
            self.silence += dsec; self.voice = 0
        self.m.tick(Signals(self.now, self.t, self.voice, self.silence))

    def run(self, n, **kw):
        for _ in range(n):
            self.step(**kw)

    def sig(self):
        return Signals(self.now, self.t, self.voice, self.silence)


def test_idle_to_arme_to_avant():
    m, _ = mk()
    d = Drv(m, at(12, 10))
    d.run(10, voice=False)
    assert m.state == State.IDLE
    d.now = at(12, 15, 1); d.step(voice=False)
    assert m.state == State.ARME and not m.stream_on
    d.now = at(12, 28, 1); d.step(voice=False)
    assert m.state == State.AVANT and m.stream_on


def test_voice_20s_starts_khutba_only_when_armed():
    m, _ = mk()
    d = Drv(m, at(12, 5))
    d.run(40, voice=True)  # voix hors fenêtre : rien
    assert m.state == State.IDLE
    d.now = at(12, 20)
    d.step(voice=True)
    assert m.state == State.ARME  # armé ; voix déjà > 20 s => démarrage anticipé au tick suivant
    d.step(voice=True)
    assert m.state == State.KHUTBA
    assert m.records[0].status == "live"


def _start_khutba(m, d):
    d.now = at(12, 29)
    d.run(25, voice=True)
    assert m.state == State.KHUTBA


def test_stop_needs_silence_plus_grace_and_resume_cancels():
    m, _ = mk(silence_stop_s=60, grace_s=30, min_khutba_s=100)
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    d.run(200, voice=True)
    d.run(61, voice=False)          # silence 61 s -> grâce
    assert m.state == State.KHUTBA
    d.run(5, voice=True)            # la voix revient pendant la grâce
    d.run(61, voice=False)
    assert m.state == State.KHUTBA                # la voix revenue a annulé la grâce : elle repart de zéro
    d.run(31, voice=False)
    assert m.state == State.PRIERE
    assert m.records[0].status == "done" and m.records[0].end_reason == "silence prolongé"


def test_no_stop_before_min_duration():
    m, _ = mk(silence_stop_s=10, grace_s=5, min_khutba_s=300)
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    d.run(100, voice=False)
    assert m.state == State.KHUTBA


def test_two_sessions_full_cycle():
    m, logs = mk(silence_stop_s=10, grace_s=5, min_khutba_s=30, prayer_min=10, terminated_hold_s=20)
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    d.run(60, voice=True); d.run(30, voice=False)
    assert m.state == State.PRIERE
    d.step(clock=11 * 60, voice=False)           # 11 min de prière
    assert m.state == State.ENTRACTE and m.session == at(13, 30)
    d.now = at(13, 16); d.voice = d.silence = 0
    d.run(25, voice=True)
    assert m.state == State.KHUTBA and m.records[1].status == "live"
    d.run(60, voice=True); d.run(20, voice=False)
    assert m.state == State.PRIERE
    d.step(clock=11 * 60, voice=False)
    assert m.state == State.TERMINE
    d.run(21, voice=False)
    assert m.state == State.FIN and not m.stream_on
    seq = [l for l in logs if l.startswith("ÉTAT")]
    names = [l.split("→")[1].split()[0] for l in seq]
    assert names == ["ARMÉ", "AVANT", "KHUTBA", "PRIÈRE", "ENTRACTE", "KHUTBA", "PRIÈRE", "TERMINÉ", "FIN"]
    assert [r.status for r in m.records] == ["done", "done"]


def test_second_session_skipped_when_no_signal():
    m, _ = mk(silence_stop_s=10, grace_s=5, min_khutba_s=30, prayer_min=10)
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    d.run(60, voice=True); d.run(20, voice=False)
    d.step(clock=11 * 60, voice=False)
    assert m.state == State.ENTRACTE
    d.now = at(14, 16)               # fenêtre 13:30+45 dépassée, aucune voix
    d.step(voice=False)
    assert m.records[1].status == "skipped"
    assert m.state == State.TERMINE


def test_manual_override_suspends_auto():
    m, _ = mk()
    d = Drv(m, at(12, 29))
    msg = m.command("start", d.sig())
    assert m.state == State.KHUTBA and m.override
    d.run(500, voice=False)           # le silence n'arrête rien en override
    assert m.state == State.KHUTBA
    m.command("auto", d.sig())
    assert not m.override
    m.command("pause", d.sig())
    assert m.state == State.PRIERE
    m.command("resume", d.sig())
    assert m.state == State.KHUTBA


def test_scene_command_and_unknown():
    m, _ = mk()
    d = Drv(m, at(12, 0))
    assert "inconnue" in m.command("scene", d.sig(), "disco")
    m.command("scene", d.sig(), "entracte")
    assert m.state == State.ENTRACTE and m.stream_on and m.scene == "entracte"


def test_extend_blocks_stop():
    m, _ = mk(silence_stop_s=10, grace_s=5, min_khutba_s=30)
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    d.run(60, voice=True)
    m.command("extend", d.sig(), 5)
    d.run(200, voice=False)
    assert m.state == State.KHUTBA
    d.run(120, voice=False)
    assert m.state == State.PRIERE


def test_janaza_excluded_from_pipeline():
    m, _ = mk()
    d = Drv(m, at(13, 5))
    m.command("janaza", d.sig(), "X")
    assert m.state == State.JANAZA
    j = [r for r in m.sessions_dicts() if r["status"] == "janaza"]
    assert j and not runs.is_khutba(j[0])
    d.run(100, voice=False)
    assert m.state == State.JANAZA     # jamais coupée automatiquement
    m.command("stop", d.sig())
    assert m.state == State.TERMINE


def test_cancel_day():
    m, _ = mk()
    d = Drv(m, at(12, 20))
    m.command("cancel", d.sig())
    assert all(r.status == "skipped" for r in m.records)
    d.step()
    assert m.state == State.FIN


def test_max_duration_guard():
    m, _ = mk(max_khutba_s=100, silence_stop_s=1e9)
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    d.run(101, voice=True)
    assert m.state == State.PRIERE
    assert m.records[0].end_reason == "durée maximale"


def test_leaving_khutba_by_hand_closes_its_session():
    m, _ = mk()
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    m.command("scene", d.sig(), "priere")
    m.command("auto", d.sig())
    assert m.records[0].status == "done"
    d.now = at(13, 29)
    d.run(25, voice=True)
    assert m.state == State.KHUTBA and m.records[1].status == "live"


def test_pause_then_start_then_auto_lets_prayer_end():
    m, _ = mk()
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    m.command("pause", d.sig())
    m.command("start", d.sig())
    assert m.records[0].status == "live"          # la pause n'a pas coupé la session
    m.command("scene", d.sig(), "priere")
    m.command("auto", d.sig())
    d.run(13, voice=False, clock=60)
    assert m.state == State.ENTRACTE and m.records[0].status == "done"


def test_stop_while_off_air_does_not_start_the_stream():
    m, _ = mk()
    d = Drv(m, at(12, 0))
    m.command("stop", d.sig())
    assert m.state == State.FIN and not m.stream_on


def test_extend_with_bad_duration_answers():
    m, _ = mk()
    d = Drv(m, at(12, 29))
    assert "invalide" in m.command("extend", d.sig(), "abc")


def test_session_spanning_an_encoder_restart_keeps_its_first_file():
    m, _ = mk()
    pos = [(0, 5.0)]
    m.pos_fn = lambda: pos[0]
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    pos[0] = (1, 30.0)                              # ffmpeg relancé : nouvel enregistrement
    m.command("stop", d.sig())
    r = m.records[0]
    assert (r.recording, r.start_pos, r.end_pos) == (0, 5.0, None)


def test_prayer_shown_early_by_hand_keeps_the_session_ahead():
    m, _ = mk()
    d = Drv(m, at(12, 20))
    m.command("scene", d.sig(), "priere")
    m.command("auto", d.sig())
    d.run(13, voice=False, clock=60)
    assert m.records[0].status == "pending" and m.session == at(12, 30)


def test_after_stop_no_session_is_left_pending():
    m, _ = mk()
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    m.command("stop", d.sig())
    d.run(50, voice=False)
    assert m.state == State.FIN
    assert [r.status for r in m.records] == ["done", "skipped"]


def test_resume_without_pause_does_nothing():
    m, _ = mk()
    d = Drv(m, at(12, 0))
    assert "pas en pause" in m.command("resume", d.sig())
    assert m.state == State.IDLE and len(m.records) == 2


def test_janaza_name_cleared_after_janaza():
    m, _ = mk()
    d = Drv(m, at(12, 29))
    m.command("janaza", d.sig(), "Ahmed")
    assert m.status()["janaza_name"] == "Ahmed"
    m.command("stop", d.sig())
    assert m.status()["janaza_name"] is None


def test_cancel_closes_the_live_khutba():
    m, _ = mk()
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    m.command("cancel", d.sig())
    r = m.records[0]
    assert r.status == "skipped" and r.end_clock is not None


def test_janaza_pause_keeps_the_name():
    m, _ = mk()
    d = Drv(m, at(12, 29))
    m.command("janaza", d.sig(), "Ahmed")
    m.command("pause", d.sig())
    m.command("resume", d.sig())
    assert m.state == State.JANAZA and m.status()["janaza_name"] == "Ahmed"


def test_auto_after_pause_resumes_automatic_mode():
    m, _ = mk()
    d = Drv(m, at(12, 0))
    m.command("pause", d.sig())
    m.command("auto", d.sig())
    d.run(16, voice=False, clock=60)
    assert m.state == State.ARME


def test_nothing_comes_back_after_fin():
    m, _ = mk()
    d = Drv(m, at(12, 0))
    m.command("stop", d.sig())
    assert m.status()["next_session"] is None
    assert "aucune session" in m.command("skip", d.sig())


def test_auto_after_janaza_hands_back_the_next_session():
    m, _ = mk()
    d = Drv(m, at(12, 29)); _start_khutba(m, d)
    m.command("janaza", d.sig(), "Ali")
    d.now = at(13, 0)
    m.command("auto", d.sig())
    assert m.state == State.ENTRACTE and m.records[-1].status == "janaza" and m.records[-1].end_clock
    d.now = at(13, 25)
    d.run(25, voice=True)
    assert m.state == State.KHUTBA and m.records[1].status == "live"
