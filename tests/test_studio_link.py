"""The orchestrator follows a live ordered from Sada Studio (manual, or a scheduled slot)."""

from __future__ import annotations

import datetime as dt

from sada import studio_link
from sada.state import Machine, MachineParams, Signals, State
from sada.studio_link import Announcement, Follower, Poller


def at(h, m=0, s=0):
    return dt.datetime(2026, 11, 20, h, m, s)


def sig(now, t=1000.0, voice=0.0, silence=0.0):
    return Signals(now=now, t=t, voice_run=voice, silence_run=silence)


MANUAL = Announcement(live=True, source="manual", type="dars", title="Cours improvisé")
SLOT = Announcement(live=True, source="schedule", type="dars", title="Tafsir du soir", slot_id="x@1")
OFF = Announcement(live=False)


def machine(*sessions):
    return Machine(list(sessions), MachineParams())


def test_manual_live_opens_an_offprogramme_session_with_type_and_title():
    m, f = machine(at(20, 0)), Follower()
    note = f.step(m, sig(at(15, 0)), MANUAL)
    assert note and "Cours improvisé" in note
    assert m.state == State.DARS and m.stream_on and m.override
    r = m.live
    assert (r.kind, r.title, r.status) == ("dars", "Cours improvisé", "live") and r.planned.startswith("studio@")
    assert m.playing is r and m.records[0].status == "pending"  # the planned 20:00 session is untouched
    assert f.step(m, sig(at(15, 0, 6)), MANUAL) is None  # same announcement: nothing to do


def test_announcement_change_renames_the_replay():
    m, f = machine(at(20, 0)), Follower()
    f.step(m, sig(at(15, 0)), MANUAL)
    f.step(m, sig(at(15, 1)), Announcement(live=True, source="manual", type="conference", title="Soirée"))
    assert (m.live.kind, m.live.title) == ("conference", "Soirée")


def test_manual_stop_ends_the_session_then_the_plan_resumes():
    m, f = machine(at(20, 0)), Follower()
    f.step(m, sig(at(15, 0), t=1000.0), MANUAL)
    note = f.step(m, sig(at(15, 20), t=2200.0), OFF)
    assert note and m.state == State.DARS_END and m.live is None  # session closed, end screen up
    done = m.records[-1]
    assert done.status == "done" and done.title == "Cours improvisé" and done.end_reason == "fin du direct Studio"
    m.tick(sig(at(15, 21), t=2210.0))  # the end screen is held first
    assert m.state == State.DARS_END and m.stream_on
    m.tick(sig(at(15, 22), t=2200.0 + m.p.dars.ended_hold_s + 1))
    assert m.state == State.IDLE and not m.stream_on  # the stream stops
    assert not m.override and m.session == at(20, 0) and m.records[0].status == "pending"  # the plan is kept


def test_khutba_manual_live_uses_the_khutba_end_screen():
    m, f = machine(), Follower()
    f.step(m, sig(at(12, 0)), Announcement(live=True, source="manual", type="khutba", title="Jumua"))
    assert m.state == State.KHUTBA
    f.step(m, sig(at(12, 30), t=1900.0), OFF)
    assert m.state == State.ENDED
    m.tick(sig(at(12, 31), t=1900.0 + m.p.khutba.ended_hold_s + 1))
    assert m.state == State.STOPPED  # nothing left in the plan: the day is over


def test_planned_session_on_air_is_never_taken_over():
    m, f = machine(at(12, 30)), Follower()
    m.tick(sig(at(12, 20), 0.0))
    m.tick(sig(at(12, 30), 30.0, voice=25.0))
    assert m.state == State.KHUTBA
    assert f.step(m, sig(at(12, 31), 40.0), MANUAL) is None
    assert m.live.planned == "12:30" and not f.owned
    f.step(m, sig(at(12, 40), 90.0), OFF)
    assert m.state == State.KHUTBA  # a stop of the studio does not end a session it did not open


def test_scheduled_slot_attaches_the_announced_type_and_title():
    m, f = machine(at(20, 0)), Follower()
    m.tick(sig(at(19, 50), 0.0))
    m.tick(sig(at(20, 0), 30.0, voice=25.0))
    assert m.live.kind == "khutba"  # a plain planned session, as the plan had it
    note = f.step(m, sig(at(20, 1), 40.0), SLOT)
    assert note and (m.live.kind, m.live.title) == ("dars", "Tafsir du soir")
    assert f.step(m, sig(at(20, 1, 6), 46.0), SLOT) is None


def test_end_of_the_slot_ends_the_planned_session_cleanly():
    m, f = machine(at(20, 0)), Follower()
    m.tick(sig(at(19, 50), 0.0))
    m.tick(sig(at(20, 0), 30.0, voice=25.0))
    f.step(m, sig(at(20, 1), 40.0), SLOT)
    note = f.step(m, sig(at(21, 0), 3600.0), OFF)
    assert note and m.live is None and m.records[0].status == "done"
    assert m.records[0].end_reason == "fin du créneau Studio"


def test_back_office_silence_never_ends_a_live():
    m, f = machine(at(20, 0)), Follower()
    f.step(m, sig(at(15, 0)), MANUAL)
    assert f.step(m, sig(at(15, 5), 1300.0), None) is None
    assert m.state == State.DARS and m.live is not None


def test_poller_keeps_the_last_answer_and_silence_gives_none():
    answers = iter([{"live": True, "source": "manual", "type": "Dars ", "title": "  A   b "}, RuntimeError("down")])

    def getter(url, token):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    assert studio_link.fetch("", 0, getter) is None  # no back office configured
    got = studio_link.fetch("http://bo", 8088, getter)
    assert got == Announcement(live=True, source="manual", type="dars", title="A b", slot_id=None)
    assert studio_link.fetch("http://bo", 8088, getter) is None  # unreachable: no change decided
    assert Poller("", 0).take() is None
    assert Announcement.parse({"live": True, "source": "other", "type": "karaoke"}) == Announcement(live=True)


def test_slot_unknown_to_the_plan_is_recorded_as_an_extra_session_and_ends_with_the_slot():
    m, f = machine(), Follower()  # the orchestrator started before the programme was edited: empty plan
    note = f.step(m, sig(at(20, 0)), SLOT)
    assert note and m.state == State.DARS and m.live.title == "Tafsir du soir" and f.owned
    assert f.step(m, sig(at(20, 1), 70.0), SLOT) is None  # nothing new to do
    f.step(m, sig(at(21, 30), 5400.0), OFF)
    assert m.state == State.DARS_END and m.records[-1].status == "done"


def test_slot_known_to_the_plan_is_left_to_the_plan():
    m, f = machine(at(20, 0)), Follower()
    assert f.step(m, sig(at(19, 58)), SLOT) is None  # the plan starts it itself, from the stream
    assert m.live is None and not f.owned
