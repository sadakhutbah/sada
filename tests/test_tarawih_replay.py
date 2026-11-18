"""Tarawih replays: each night its own entry (night number, reciters, chapters), playable but never
transcribed, clipped nor translated, listed apart from the khutbas; an audio night over its slate."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import client, one

from sada import runs
from sada.backoffice import importer, models

RUN = "20270220_190000"


def _night(env, mode="audio", marks=None, status="done"):
    """A run holding one finished tarawih night (session index 0), recording present."""
    root = Path(env["root"])
    rec = root / "rec.mkv"
    rec.write_bytes(b"\0" * 64)
    state = root / "state" / RUN
    state.mkdir(parents=True, exist_ok=True)
    session = {
        "index": 0,
        "kind": "tarawih",
        "title": "Tarawih",
        "status": status,
        "mode": mode,
        "night": 13,
        "reciters": ["Imam A", "Imam B"],
        "reciter_marks": marks or [],
        "start_pos": 100.0,
        "end_pos": 4100.0,
        "start_clock": "20:10:00",
        "end_clock": "21:16:40",
        "recording": 0,
    }
    (state / runs.SESSIONS).write_text(json.dumps({"sessions": [session], "recordings": [str(rec)]}))
    return rec


def _mosque(env):
    return dict(one(env["conn"], models.Mosque, id=env["mid"]))


def test_une_nuit_devient_une_entree_tarawih(env):
    _night(env, marks=[{"at": 0.0, "name": "Imam A"}, {"at": 1800.0, "name": "Imam B"}])
    m = _mosque(env)
    assert importer.import_tarawih(env["conn"], m, RUN, extract_video=False, log=lambda *a: None) == 1
    assert importer.import_tarawih(env["conn"], m, RUN, extract_video=False, log=lambda *a: None) == 0  # idempotent
    k = one(env["conn"], models.Khutba, run_id=RUN)
    assert k["kind"] == "tarawih" and k["session_no"] == 13 and k["date"] == "2027-02-20"
    assert k["speaker"] == "Imam A, Imam B"
    assert json.loads(k["chapters"])["segments"] == [
        {"name": "Imam A", "start": 0.0, "end": 1800.0},
        {"name": "Imam B", "start": 1800.0, "end": 4000.0},
    ]
    assert one(env["conn"], models.Clip, khutba_id=k["id"]) is None  # no clip, no transcript


def test_sans_changement_de_recitant_pas_de_chapitres():
    out = importer.reciter_chapters({"reciters": ["Imam A", "Imam B"], "start_pos": 0.0, "end_pos": 60.0})
    assert out == {"reciters": ["Imam A", "Imam B"], "segments": []}


def test_nuit_non_terminee_pas_importee(env):
    _night(env, status="live")
    assert importer.import_tarawih(env["conn"], _mosque(env), RUN, extract_video=False, log=lambda *a: None) == 0


def test_les_tarawih_restent_hors_des_khutbas(env):
    _night(env)
    importer.import_tarawih(env["conn"], _mosque(env), RUN, extract_video=False, log=lambda *a: None)
    kid = one(env["conn"], models.Khutba, run_id=RUN)["id"]
    c = client(env, "admin")
    kinds = lambda q: {s["kind"] for s in c.get(f"/api/v1/sermons{q}").json()["items"]}  # noqa: E731
    assert "tarawih" not in kinds("") and "tarawih" not in kinds("?kind=khutba")
    assert kinds("?kind=tarawih") == {"tarawih"}
    assert all(r["kind"] != "tarawih" for r in c.get("/api/v1/dashboard").json()["recent"])
    # the khutba routes do not see a night: no transcript, clips, subtitles nor dressing
    assert c.get(f"/api/v1/sermons/{kid}").status_code == 404
    assert c.ecrire(f"/sermons/{kid}/clips", {"start": 0, "end": 30, "kind": "verset"}).status_code == 404
    night = c.get(f"/api/v1/tarawih/nights/{kid}").json()
    assert night["night"]["session_no"] == 13 and night["mode"] == "audio"
    assert night["reciters"] == ["Imam A", "Imam B"] and night["segments"] == []
    assert c.get(f"/api/v1/tarawih/nights/{env['kid']}").status_code == 404  # a khutba is not a night
    assert c.get("/api/v1/session").json()["counts"]["tarawih"] == 1  # the navigation shows the page


def test_nuit_audio_rejouee_sur_l_ecran_fixe(env, monkeypatch):
    """An audio-only night: the picture comes from the slate, only the sound of the recording."""
    rec = _night(env, mode="audio")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(importer.subprocess, "run", fake_run)
    monkeypatch.setattr(importer, "_ffmpeg", lambda: "ffmpeg")
    importer.import_tarawih(env["conn"], _mosque(env), RUN, slate=b"PNG", log=lambda *a: None)
    cmd = calls[0]
    assert cmd[cmd.index("-i") + 1].endswith(".png")  # first input: the slate
    assert "1:a:0" in cmd and "1:v:0" not in cmd and "0:v:0" in cmd  # the camera is never read
    assert str(rec) in cmd
    assert one(env["conn"], models.Khutba, run_id=RUN)["video_path"].endswith("video.mp4")


def test_nuit_filmee_rejouee_avec_la_camera(env, monkeypatch):
    _night(env, mode="video")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(importer.subprocess, "run", fake_run)
    monkeypatch.setattr(importer, "_ffmpeg", lambda: "ffmpeg")
    importer.import_tarawih(env["conn"], _mosque(env), RUN, slate=b"PNG", log=lambda *a: None)
    assert "0:v:0" in calls[0] and not any(str(x).endswith(".png") for x in calls[0])


def test_ecran_fixe_de_la_mosquee(env):
    png = importer._tarawih_slate(env["conn"], _mosque(env))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_l_import_periodique_prend_les_nuits(env):
    _night(env)
    importer.scan(env["conn"], _mosque(env), extract_video=False, log=lambda *a: None)
    assert one(env["conn"], models.Khutba, run_id=RUN)["kind"] == "tarawih"


def test_l_enregistrement_du_moteur_porte_la_nuit():
    """What the engine writes in sessions.json is what the import reads."""
    import datetime as dt

    from sada.state import Machine, MachineParams

    m = Machine(
        [dt.datetime(2027, 2, 20, 19, 50)],
        MachineParams(),
        extras=[{"kind": "tarawih", "mode": "audio", "night": 13, "reciters": ["Imam A"]}],
    )
    rec = m.sessions_dicts()[0]
    assert rec["kind"] == "tarawih" and rec["mode"] == "audio" and rec["night"] == 13
    assert rec["reciters"] == ["Imam A"] and rec["reciter_marks"] == []
