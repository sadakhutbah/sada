"""Entity states: one shape, stable codes, stuck detection."""

import datetime as dt

from sada.backoffice import states

NOW = dt.datetime(2026, 11, 15, 14, 0, 0)


def _clip(**kw):
    return {"id": 10, "status": "a_valider", "regen_state": "", "regen_error": "", "needs_regen": 0,
            "updated_at": "2026-11-15T13:55:00", **kw}


def test_clip_ready():
    assert states.clip_state(_clip())["level"] == "ok"


def test_clip_regenerating_then_stuck():
    s = states.clip_state(_clip(regen_state="en_cours"), now=NOW)
    assert (s["level"], s["code"], s["stuck"]) == ("running", "clip.regenerating", False)
    s = states.clip_state(_clip(regen_state="en_cours", updated_at="2026-11-15T13:00:00"), now=NOW)
    assert s["stuck"] is True


def test_clip_failed_carries_reason_and_action():
    s = states.clip_state(_clip(regen_state="erreur", regen_error="ffmpeg exited 1"), can_regenerate=True)
    assert s["level"] == "failed" and s["detail"] == "ffmpeg exited 1"
    assert s["actions"] == [{"key": "regenerate", "url": "/api/v1/clips/10/regenerate"}]


def test_clip_outdated_and_published_outdated():
    assert states.clip_state(_clip(needs_regen=1))["code"] == "clip.outdated"
    assert states.clip_state(_clip(needs_regen=1, status="publie"))["code"] == "clip.published_outdated"


def test_sermon_processing_takes_priority_over_clips():
    clips = [states.clip_state(_clip(needs_regen=1))]
    s = states.sermon_state({"etat": "en_cours", "debut": "2026-11-15T13:00:00"}, clips, now=NOW)
    assert s["code"] == "sermon.processing" and s["children"]["clips"]["attention"] == 1


def test_sermon_processing_failed_and_stuck():
    s = states.sermon_state({"etat": "echec", "erreur": "boom"}, [])
    assert (s["level"], s["detail"]) == ("failed", "boom")
    s = states.sermon_state({"etat": "en_cours", "debut": "2026-11-15T09:00:00"}, [], now=NOW)
    assert s["stuck"] is True


def test_sermon_without_processing_reflects_clips():
    assert states.sermon_state(None, [])["code"] == "sermon.ready"
    bad = [states.clip_state(_clip(regen_state="erreur"))]
    assert states.sermon_state({"etat": "termine"}, bad)["code"] == "sermon.clips_failed"
