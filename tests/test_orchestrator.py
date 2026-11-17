"""Back office unreachable at start: the day is not left on the default times.

The orchestrator retries the times and the programme in the background and replans the sessions
still to come as soon as the back office answers (see Orchestrator._retry_plan / Machine.replan).
"""

import datetime as dt
from types import SimpleNamespace

from sada import events as events_mod
from sada import orchestrator as orch
from sada.state import Machine, MachineParams, Signals, State

D = dt.date(2026, 11, 14)  # Saturday
TIMES = ["06:00", "13:00", "15:30", "17:12", "18:45"]  # fajr, dhuhr, asr, maghrib, isha


def at(h, m=0, s=0):
    return dt.datetime.combine(D, dt.time(h, m, s))


def sig(now, t=1000.0, voice=0.0):
    return Signals(now, t, voice, 0.0)


def dars(title="Tafsir Al-Kahf"):
    """Dars of the day, after maghrib: its start only exists with the real (Mawaqit) times."""
    return events_mod.parse(
        {
            "kind": "dars",
            "title": title,
            "speaker": "Imam Abdallah",
            "series": title,
            "session": 7,
            "recurrence": {"weekday": "samedi", "after": "maghrib"},
        }
    )


def dars_event(title="Tafsir Al-Kahf"):
    """Same dars, in the shape the machine expects (event of a session)."""
    return events_mod.instances_on([dars(title)], D, {"maghrib": "17:12"})[0].as_event()


def orchestrator(down):
    """Orchestrator stripped to the schedule, as it stands after a start on the fallback."""
    o = object.__new__(orch.Orchestrator)
    o.cfg = SimpleNamespace(
        backoffice_url="http://127.0.0.1:8090", api_port=8088, jumua_times_override=[], sim_start="12:00"
    )
    # simulated start: the khutba times apply on this Saturday too
    o.times, o.program, o.mawaqit_data, o.instances = ["12:30", "13:30"], [], None, []
    o._plan_day, o._rolled = D, False
    o._stale, o._times_fallback, o._program_remote = True, True, False
    o._retry_delay, o._retry_at = orch.RETRY_DELAY_S, 0.0
    o._retry_thread, o._fetched = None, None
    sessions, extras = o.day_plan(D)
    o.machine = Machine(sessions, MachineParams(), extras=extras)

    def fake_times(url, port, fetch=None, log=None):
        if down[0]:
            return None
        return {
            "name": "Test",
            "jumua": "13:00",
            "jumua_list": ["13:00"],
            "origin": "live",
            "times": TIMES,
        }

    def fake_program(url, port, fetch=None, log=None):
        return None if down[0] else [dars()]

    o.fetched_times, o.fetched_program = fake_times, fake_program
    return o


def test_back_office_down_then_up_replans_the_day(monkeypatch):
    down = [True]
    o = orchestrator(down)
    monkeypatch.setattr(orch.mawaqit, "from_backoffice", lambda *a, **k: o.fetched_times(*a, **k))
    monkeypatch.setattr(orch.events_mod, "from_backoffice", lambda *a, **k: o.fetched_program(*a, **k))
    assert o.machine.sessions == [at(12, 30), at(13, 30)]  # the fallback, until tomorrow

    # first attempt: the back office is still down, nothing moves
    o._retry_plan(sig(at(12, 5)))
    o._retry_thread.join(10)
    assert o._fetched is None
    assert o.machine.sessions == [at(12, 30), at(13, 30)]
    assert o._stale  # still to be retried

    # 30 s later the back office answers: times and programme arrive together
    down[0] = False
    o._retry_at = 0.0
    o._retry_plan(sig(at(12, 6)))
    o._retry_thread.join(10)
    o._retry_plan(sig(at(12, 7)))  # the loop applies the result it just collected

    assert o.times == ["13:00"]
    assert o.mawaqit_data["times"] == TIMES
    assert o.machine.sessions == [at(13, 0), at(17, 12)]  # khutba 13:00 + dars after maghrib
    assert o.machine.records[1].kind == "dars"
    assert o.machine.records[1].title == "Tafsir Al-Kahf"
    assert not o._stale  # the plan is the real one: no more retries

    o._retry_plan(sig(at(12, 8)))
    assert o._retry_thread is None or not o._retry_thread.is_alive()


def test_replan_keeps_the_session_in_progress():
    """A session is playing when the real timetable arrives: it is never restarted nor cut."""
    logs = []
    m = Machine([at(12, 30), at(13, 30)], MachineParams(), log=logs.append)
    m.tick(sig(at(12, 20), 0.0))  # armed
    m.tick(sig(at(12, 30), 30.0, 25.0))  # sustained voice: the khutba is under way
    assert m.state == State.KHUTBA and m.live.planned == "12:30"

    added = m.replan([at(12, 45), at(17, 12)], [{}, dars_event()], sig(at(12, 35), 400.0))

    assert added == 2
    assert m.live is not None and m.live.planned == "12:30"  # in progress, untouched
    assert m.sessions == [at(12, 30), at(12, 45), at(17, 12)]  # 13:30 (never started) replaced
    assert m.idx == 1
    assert m.rec.kind == "khutba"


def test_replan_reopens_a_day_already_closed():
    """The day ended on the fallback hours; a session arrives for tonight: the live resumes."""
    m = Machine([at(12, 30)], MachineParams())
    m.tick(sig(at(13, 30), 0.0))  # armed, then the window is closed: skipped, nothing left
    m.tick(sig(at(13, 31), 1.0))
    assert m.state == State.STOPPED

    added = m.replan([at(17, 12)], [dars_event()], sig(at(16, 0), 100.0))

    assert added == 1
    assert m.state == State.IDLE
    assert m.session == at(17, 12)
    assert m.rec.kind == "dars"


def test_replan_ignores_sessions_whose_window_is_closed():
    """Arriving too late must not start a session in the past."""
    m = Machine([at(12, 30)], MachineParams())
    m.tick(sig(at(12, 31), 0.0))
    assert m.replan([at(12, 0)], [{}], sig(at(14, 0), 100.0)) == 0


def test_khutbas_only_on_friday():
    """Mawaqit's jumua times are not a daily plan: outside Friday only the programme remains."""
    o = object.__new__(orch.Orchestrator)
    o.cfg = SimpleNamespace(jumua_times_override=[], sim_start=None)
    o.times, o.program, o.mawaqit_data = ["12:30", "13:30"], [], {"times": TIMES}
    assert o.day_plan(dt.date(2026, 11, 17)) == ([], [])  # Tuesday
    assert [s.strftime("%H:%M") for s in o.day_plan(dt.date(2026, 11, 20))[0]] == ["12:30", "13:30"]
    o.program = [dars()]
    assert [s.strftime("%H:%M") for s in o.day_plan(D)[0]] == ["17:12"]  # Saturday: the dars alone
    o.cfg.jumua_times_override = ["12:30"]  # forced by hand: any day
    o.program = []
    assert len(o.day_plan(dt.date(2026, 11, 17))[0]) == 2


def test_midnight_plans_the_new_day_then_refreshes_its_times(monkeypatch):
    """The process runs for days: at midnight the plan of the new day replaces yesterday's."""
    down = [False]
    o = orchestrator(down)
    o._stale = False
    monkeypatch.setattr(orch.mawaqit, "from_backoffice", lambda *a, **k: o.fetched_times(*a, **k))
    monkeypatch.setattr(orch.events_mod, "from_backoffice", lambda *a, **k: o.fetched_program(*a, **k))
    o._plan_day = D - dt.timedelta(days=1)
    o.machine.records[0].status = "done"  # yesterday's session, played

    o._roll_day(sig(at(0, 1)))
    assert o._plan_day == D and o._stale and o._rolled
    assert o.machine.sessions == [at(12, 30), at(13, 30)]  # at once, with what is known
    assert all(r.status == "pending" for r in o.machine.records)

    o._retry_plan(sig(at(0, 2)))
    o._retry_thread.join(10)
    o._retry_plan(sig(at(0, 3)))
    assert o.machine.sessions == [at(13, 0), at(17, 12)]  # today's times, the day planned whole
    assert not o._stale and not o._rolled


def test_new_day_waits_for_a_session_running_over_midnight():
    m = Machine([at(23, 0)], MachineParams())
    m.state = State.DARS
    assert not m.new_day([at(13, 0)], None, sig(at(0, 1)))
    m.state = State.ENDED
    assert m.new_day([at(13, 0)], None, sig(at(0, 2)))
    assert m.state == State.IDLE and m.sessions == [at(13, 0)]


def test_start_waits_for_the_back_office_then_gives_up():
    """Deployed together, the back office often boots second: the start waits for it, bounded."""
    t = [0.0]
    answers = iter([False, False, True])
    assert orch.wait_for_backoffice(
        "http://bo", 90, probe=lambda: next(answers), sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]
    )
    assert t[0] == 3.0  # 1 s, then 2 s

    t[0] = 0.0
    assert not orch.wait_for_backoffice(
        "http://bo", 30, probe=lambda: False, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]
    )
    assert t[0] == 30.0  # never beyond the limit
    assert not orch.wait_for_backoffice("", 90, probe=lambda: 1 / 0)  # no back office: no wait
