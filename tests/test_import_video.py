"""Import d'une vidéo enregistrée hors direct : envoi -> run d'une session -> file de traitement."""

import json
import subprocess
from pathlib import Path

import pytest
from test_backoffice import client

from sada import runs
from sada.backoffice import importer, traitement

URL = "/api/v1/imports/video"


def _video(tmp_path: Path, son: bool = True) -> bytes:
    ff = importer._ffmpeg()
    if not ff:
        pytest.skip("ffmpeg absent")
    out = tmp_path / ("v.mp4" if son else "muet.mp4")
    cmd = [ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2"]
    if son:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest"]
    subprocess.run(cmd + [str(out)], check=True)
    return out.read_bytes()


def _post(c, data, **q):
    q.setdefault("filename", "cours.mp4")
    return c.post(URL, params=q, content=data, headers={"X-CSRF": c.csrf, "content-type": "application/octet-stream"})


def test_import_cree_un_run_en_file(env, tmp_path):
    c = client(env, "admin")
    r = _post(
        c, _video(tmp_path), kind="dars", title="Tafsir Al-Kahf", speaker="Imam X", session_no="3", date="2026-11-03"
    )
    assert r.status_code == 200, r.text
    rid = r.json()["run"]
    assert rid.startswith("20261103_")
    rd = (
        env["root"] / "state" / rid
        if "root" in env
        else Path(env["conn"].execute("SELECT state_dir FROM mosques").fetchone()[0]) / rid
    )
    data = runs.load(rd / runs.SESSIONS)
    s = data["sessions"][0]
    assert s["status"] == "done" and s["kind"] == "dars" and s["title"] == "Tafsir Al-Kahf" and s["session_no"] == 3
    assert 1.5 < s["end_pos"] < 2.5
    assert Path(data["recordings"][0]).exists()
    assert traitement.lire(rd)["0"]["etat"] == "attente"
    mo = dict(env["conn"].execute("SELECT * FROM mosques").fetchone())
    assert (rd, 0) in traitement.a_traiter(mo)  # pris malgré une date ancienne
    assert importer.khutba_label(dict(s, index=0)).endswith("Tafsir Al-Kahf · séance 3")


@pytest.mark.parametrize(
    "q, data_kind, msg",
    [
        ({"filename": "a.exe"}, "ok", "format"),
        ({"kind": "fete"}, "ok", "type"),
        ({"date": "2999-01-01"}, "ok", "future"),
        ({"session_no": "trois"}, "ok", "number"),
        ({}, "muet", "sound"),
        ({}, "texte", "not a valid video"),
        ({}, "vide", "Empty file"),
    ],
)
def test_import_refuse(env, tmp_path, q, data_kind, msg):
    c = client(env, "admin")
    data = {"ok": b"x" * 10 if q else None, "muet": None, "texte": b"pas une video", "vide": b""}[data_kind]
    if data_kind == "muet":
        data = _video(tmp_path, son=False)
    if data is None:
        data = b"x"
    r = _post(c, data, **q)
    assert r.status_code == 422 and msg in r.text, r.text
    sortie = Path(env["conn"].execute("SELECT output_dir FROM mosques").fetchone()[0])
    assert not any((d / "source").exists() for d in sortie.iterdir())  # rien ne reste d'un refus


def test_import_droits_et_csrf(env, tmp_path):
    vid = b"x"
    sans = client(env, "admin")
    r = sans.post(URL, params={"filename": "a.mp4"}, content=vid)
    assert r.status_code == 403
    assert _post(client(env, "imam"), vid).status_code == 403


def test_audit(env, tmp_path):
    c = client(env, "admin")
    assert _post(c, _video(tmp_path), title="Cours").status_code == 200
    a = env["conn"].execute("SELECT details FROM audit WHERE action = 'import.video'").fetchone()
    assert a and "Cours" in a[0]
    json.loads(a[0])
