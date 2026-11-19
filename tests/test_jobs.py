"""Background jobs: JobStatus values, double start refused, recovery after an interruption."""

import json
import os
import threading

import pytest

pytest.importorskip("fastapi")
from conftest import client, clip_ids, insert, one, set_clip  # noqa: E402

from sada.backoffice import content, habillage, models, traduction  # noqa: E402
from sada.backoffice.jobs import Job, JobStatus, JsonFileStore  # noqa: E402


def test_status_values_are_the_stored_strings():
    # Persisted in the database / JSON files and read by the front end: never rename.
    assert {s.name: s.value for s in JobStatus} == {
        "PENDING": "attente",
        "RUNNING": "en_cours",
        "READY": "pret",
        "FAILED": "echec",
        "DONE": "termine",
        "ERROR": "erreur",
    }
    assert JobStatus.RUNNING == "en_cours" and json.dumps({"s": JobStatus.READY}) == '{"s": "pret"}'
    assert str(JobStatus.FAILED) == "echec"


# ---------------------------------------------------------------- base class


def _json_job(tmp_path):
    return Job(
        JsonFileStore(lambda ref: tmp_path / f"{ref['id']}.json"),
        busy_message=lambda: "busy",
    )


def test_double_start_refused_then_released(tmp_path):
    job, ref = _json_job(tmp_path), {"id": 1}
    gate, started = threading.Event(), threading.Event()

    def work():
        started.set()
        gate.wait(5)
        job.mark_ready(None, ref)

    job.start(None, ref, work)
    assert started.wait(5)
    with pytest.raises(ValueError, match="busy"):
        job.start(None, ref, work)
    # another entity is not blocked
    job.start(None, {"id": 2}, lambda: None, background=False)
    gate.set()
    for t in threading.enumerate():
        if t.name.startswith("job-"):
            t.join(5)
    assert job.read(None, ref)["status"] == "pret"
    job.start(None, ref, lambda: None, background=False)  # idle again (state rewritten as running)


def test_failed_before_spawn_releases_the_guard(tmp_path):
    job, ref = _json_job(tmp_path), {"id": 1}

    def boom():
        raise RuntimeError("audit down")

    with pytest.raises(RuntimeError):
        job.start(None, ref, lambda: None, before_spawn=boom)
    assert not job._active


def test_dead_pid_is_reported_failed(tmp_path):
    job, ref = _json_job(tmp_path), {"id": 1}
    store = job.store
    store.write(None, ref, None, None, {"status": "en_cours", "pid": 999999})
    row = job.read(None, ref)
    assert row["status"] == "echec" and "interrupted" in row["error"]
    assert not job.is_running(None, ref)
    store.write(None, ref, JobStatus.RUNNING, None, {})  # records our own pid
    assert job.is_running(None, ref)


# ---------------------------------------------------------------- translation


def _khutba(env) -> dict:
    return one(env["conn"], models.Khutba, id=env["kid"])


def _state(env, **fields) -> None:
    JsonFileStore(traduction.fichier).write(None, _khutba(env), None, None, fields)


def test_translation_double_start_refused(env):
    k, user = _khutba(env), {"id": env["ids"]["admin"]}
    _state(env, status="en_cours", pid=os.getpid())
    with pytest.raises(ValueError, match="already being translated"):
        traduction.lancer(env["conn"], str(env["path"]), k, user, background=False)
    # same process, state file unreadable: the in-process guard still refuses
    traduction.fichier(k).unlink()
    traduction.JOB._active.add(k["id"])
    try:
        with pytest.raises(ValueError, match="already being translated"):
            traduction.lancer(env["conn"], str(env["path"]), k, user, background=False)
    finally:
        traduction.JOB._active.discard(k["id"])


def test_translation_recovers_after_interruption(env, monkeypatch):
    k, user = _khutba(env), {"id": env["ids"]["admin"]}
    _state(env, status="en_cours", progress=0.4, pid=999999)
    assert traduction.etat(k)["status"] == "echec"
    assert "interrupted" in traduction.etat(k)["error"]
    # the interrupted run no longer blocks a new one
    ran = []
    monkeypatch.setattr(traduction, "_traduire", lambda db_path, kid: ran.append(kid))
    out = traduction.lancer(env["conn"], str(env["path"]), k, user, background=False)
    assert ran == [k["id"]] and out["status"] == "en_cours"


# ---------------------------------------------------------------- clip regeneration


def test_clip_regeneration_double_start_refused(env, monkeypatch):
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    conn, cid = env["conn"], clip_ids(env)[0]
    set_clip(conn, cid, needs_regen=1, regen_state="en_cours")
    clip = one(conn, models.Clip, id=cid)
    with pytest.raises(ValueError, match="already in progress"):
        content.start_regeneration(conn, str(env["path"]), clip, {"id": 1}, background=False)
    assert one(conn, models.Clip, id=cid)["regen_state"] == "en_cours"


def test_clip_regeneration_recovered_at_boot(env, monkeypatch):
    conn, cid = env["conn"], clip_ids(env)[0]
    set_clip(conn, cid, regen_state="en_cours")
    content.recover_interrupted(conn)
    clip = one(conn, models.Clip, id=cid)
    assert clip["regen_state"] == "erreur" and "interrupted" in clip["regen_error"]
    # and the clip can be regenerated again
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    ran = []
    monkeypatch.setattr(content, "_render", lambda db_path, clip_id: ran.append(clip_id))
    content.start_regeneration(conn, str(env["path"]), clip, {"id": 1}, background=False)
    assert ran == [cid] and one(conn, models.Clip, id=cid)["regen_state"] == "en_cours"


# ---------------------------------------------------------------- habillage


def test_habillage_double_start_refused(env):
    insert(
        env["conn"],
        models.Habillage,
        mosque_id=env["mid"],
        khutba_id=env["kid"],
        status="en_cours",
        updated_at="x",
    )
    c = client(env, "responsable")
    r = c.ecrire(f"/sermons/{env['kid']}/habillage", {"options": {"title": "T", "speaker": "S"}})
    assert r.status_code == 422 and "already running" in r.json()["message"]
    assert one(env["conn"], models.Habillage)["status"] == "en_cours"


def test_habillage_recovered_at_boot_then_can_restart(env, monkeypatch):
    conn = env["conn"]
    insert(conn, models.Habillage, mosque_id=env["mid"], khutba_id=env["kid"], status="en_cours", updated_at="x")
    habillage.recover_interrupted(conn)
    h = one(conn, models.Habillage)
    assert h["status"] == "echec" and "interrupted" in h["error"]
    assert not habillage.JOB.is_running(conn, _khutba(env))
