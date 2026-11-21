"""Studio planning: slots from the existing schedule (Friday khutba, programme), time zone, order state machine."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from conftest import one

from sada.backoffice import events_store, models, prayer_times, studio_plan
from sada.backoffice.studio_plan import Slot, decide, to_utc, upcoming

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.UTC


def ts(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def mosque(env):
    return one(env["conn"], models.Mosque, id=env["mid"])


# ------------------------------------------------------------------ time zone


def test_wall_clock_to_utc_follows_the_clock_changes():
    assert to_utc(dt.datetime(2027, 3, 23, 20, 0), PARIS) == dt.datetime(2027, 3, 23, 19, 0, tzinfo=UTC)  # winter
    assert to_utc(dt.datetime(2027, 3, 30, 20, 0), PARIS) == dt.datetime(2027, 3, 30, 18, 0, tzinfo=UTC)  # summer
    # 02:30 does not exist on 2027-03-28: it is shifted forward to 03:30 (summer time)
    gap = to_utc(dt.datetime(2027, 3, 28, 2, 30), PARIS)
    assert gap.astimezone(PARIS).strftime("%H:%M") == "03:30"
    # 02:30 happens twice on 2027-10-31: the first one (summer time) is taken
    twice = to_utc(dt.datetime(2027, 10, 31, 2, 30), PARIS)
    assert twice == dt.datetime(2027, 10, 31, 0, 30, tzinfo=UTC)


def test_weekly_dars_keeps_its_local_hour_across_the_spring_change(env):
    events_store.write(
        env["conn"],
        env["mid"],
        [
            {
                "uid": "w",
                "kind": "dars",
                "title": "Tafsir",
                "recurrence": {"weekday": "mardi", "time": "20:00"},
                "max_min": 60,
            }
        ],
    )
    slots = upcoming(env["conn"], mosque(env), ts(2027, 3, 22, 12), days=10)
    assert [s.starts for s in slots] == [
        dt.datetime(2027, 3, 23, 19, 0, tzinfo=UTC),  # Tuesday, CET
        dt.datetime(2027, 3, 30, 18, 0, tzinfo=UTC),  # the next Tuesday, CEST: one hour earlier in UTC
    ]
    assert all(s.starts.astimezone(PARIS).strftime("%H:%M") == "20:00" for s in slots)
    assert slots[0].key != slots[1].key and slots[0].kind == "dars" and slots[0].title == "Tafsir"


def test_slot_length_is_real_elapsed_time_across_the_autumn_change(env):
    events_store.write(
        env["conn"],
        env["mid"],
        [{"uid": "n", "kind": "conference", "title": "Nuit", "date": "2027-10-31", "time": "02:30", "max_min": 120}],
    )
    (s,) = upcoming(env["conn"], mosque(env), ts(2027, 10, 30, 12), days=3)
    assert s.ends - s.starts == dt.timedelta(minutes=120)
    assert s.ends.astimezone(PARIS).strftime("%H:%M") == "03:30"  # two real hours later, the clock went back once


def test_friday_khutba_comes_from_the_prayer_settings(env):
    m = mosque(env)
    prayer_times.save(env["conn"], m, source="manual", public=False, jumua=["13:00", "14:00"], cfg=None)
    slots = upcoming(env["conn"], m, ts(2027, 3, 26, 8), days=8)  # a Friday morning
    assert [(s.kind, s.title) for s in slots] == [("khutba", "Khutba du vendredi")] * 4
    assert [s.starts.hour for s in slots] == [12, 13, 11, 12]  # CET on the 26th, CEST on April 2nd
    assert slots[0].ends - slots[0].starts == dt.timedelta(minutes=60)
    assert not [s for s in slots if s.starts.astimezone(PARIS).weekday() != 4]


def test_a_slot_running_past_midnight_is_still_listed(env):
    events_store.write(
        env["conn"],
        env["mid"],
        [{"uid": "late", "kind": "dars", "title": "Veillée", "date": "2027-06-10", "time": "23:30", "max_min": 90}],
    )
    now = ts(2027, 6, 10, 22, 30)  # 00:30 on the 11th in Paris: the day changed, the slot has not ended
    (s,) = upcoming(env["conn"], mosque(env), now, days=1)
    assert s.starts.timestamp() < now < s.ends.timestamp()


def test_unreadable_programme_entries_are_not_planned(env):
    events_store.write(
        env["conn"], env["mid"], [{"uid": "bad", "kind": "dars", "title": "x"}]
    )  # neither date nor recurrence
    assert upcoming(env["conn"], mosque(env), ts(2027, 3, 22, 12)) == []


# ------------------------------------------------------------------ the order (state machine)


def slot(start, minutes=60, uid="a", kind="dars", title="T"):
    s = dt.datetime.fromtimestamp(start, UTC)
    return Slot(uid, "programme", kind, title, s, s + dt.timedelta(minutes=minutes))


T0 = ts(2027, 5, 4, 18, 0)


def test_order_standby_then_live_from_the_margin_to_the_end():
    s = slot(T0)
    assert decide(T0 - 600, [s], None, set(), {}, 120).command == "standby"
    early = decide(T0 - 119, [s], None, set(), {}, 120)
    assert early.command == "live" and early.source == "schedule" and early.code == "slot"
    assert decide(T0 + 3599, [s], None, set(), {}, 120).command == "live"
    assert decide(T0 + 3600, [s], None, set(), {}, 120).command == "standby"


def test_order_announces_the_next_slot():
    a, b = slot(T0, uid="a", title="A"), slot(T0 + 7200, uid="b", title="B")
    o = decide(T0 - 1000, [a, b], None, set(), {}, 120)
    assert o.next["title"] == "A" and o.next["starts_in_s"] == 1000
    o = decide(T0 + 10, [a, b], None, set(), {}, 120)
    assert o.current["title"] == "A" and o.next["title"] == "B"


def test_manual_live_wins_over_every_slot():
    a = slot(T0, uid="a", title="Planifié")
    manual = {"kind": "khutba", "title": "Manuel", "started_at": T0 - 60}
    for now in (T0 - 30, T0 + 10, T0 + 7200):  # before, during and after the slot
        o = decide(now, [a], manual, set(), {}, 120)
        assert o.command == "live" and o.source == "manual" and o.current["title"] == "Manuel"


def test_stopped_slot_stays_standby_until_the_next_one():
    a, b = slot(T0, uid="a"), slot(T0 + 7200, uid="b", title="B")
    stopped = {a.key}
    for now in (T0 + 10, T0 + 1800, T0 + 3500):
        o = decide(now, [a, b], None, stopped, {}, 120)
        assert o.command == "standby" and o.code == "slot_stopped" and o.next["title"] == "B"
    assert decide(T0 + 7200 - 100, [a, b], None, stopped, {}, 120).command == "live"


def test_announced_title_replaces_the_planned_one():
    a = slot(T0, title="Planifié")
    o = decide(T0 + 5, [a], None, set(), {a.key: {"kind": "khutba", "title": "Annoncé"}}, 120)
    assert (o.current["type"], o.current["title"]) == ("khutba", "Annoncé")


def test_settings_are_validated_and_defaults_apply(env):
    c = env["conn"]
    assert studio_plan.settings(c, env["mid"])["start_margin_s"] == 120
    assert studio_plan.save_settings(c, env["mid"], {"start_margin_s": "300", "dars_min": 45})["dars_min"] == 45
    for bad in ({"start_margin_s": 5}, {"khutba_min": "abc"}):
        try:
            studio_plan.save_settings(c, env["mid"], bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert studio_plan.settings(c, env["mid"])["start_margin_s"] == 300
