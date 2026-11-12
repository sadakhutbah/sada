"""Clips chosen by a human: creation on a khutba, bounds moved on a proposed clip, invalid bounds refused.

Same circuit as the model's clips: rendered with subtitles (make_clip, through the regeneration
mechanism), "to validate", audit, then publication once validated.
"""
from pathlib import Path

import pytest
from test_backoffice import client, clip_ids  # noqa: F401

from sada.backoffice import content, db
from sada.pipeline import clips as clipmod


@pytest.fixture
def rendu(monkeypatch):
    """ffmpeg simulated: the real build_ass runs, the render writes a small file and remembers its window."""
    vus = []

    def fake_render(src, off, w, ass, out, log=print, crop=None, fade_out=None):
        vus.append({"src": src, "off": off, "w": dict(w), "ass": (Path(out).parent / ass).read_text()})
        Path(out).write_bytes(b"clip rendu")
    monkeypatch.setattr(clipmod, "render_clip", fake_render)
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))
    return vus


def _clip(conn, cid):
    return db.one(conn, "SELECT * FROM clips WHERE id = ?", cid)


def test_creation_manuelle_du_clip_jusqu_a_la_publication(env, rendu):
    conn, kid = env["conn"], env["kid"]
    c = client(env, "responsable")
    d = c.get(f"/api/v1/sermons/{kid}").json()["manual_clip"]
    assert d["allowed"] and "extrait" in d["kinds"] and d["max"] == content.MAX_CLIP

    r = c.ecrire(f"/sermons/{kid}/clips", {"start": "3,5", "end": "15", "kind": "rappel", "title": "  La  patience "})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    clip = _clip(conn, cid)
    # rendered at once (version 1), from the same source as the model's clips, waiting for a human
    assert (clip["origin"], clip["kind"], clip["title"]) == ("manuel", "rappel", "La patience")
    assert (clip["start"], clip["end"], clip["idx"]) == (3.5, 15.0, 3)
    assert clip["version"] == 1 and clip["needs_regen"] == 0 and clip["status"] == "a_valider"
    assert clip["created_by"] == env["ids"]["responsable"] and Path(clip["file_path"]).exists()
    assert clip["file_path"].endswith("clip3_rappel_A-VALIDER.mp4")
    assert rendu[-1]["off"] == 100.0 and rendu[-1]["w"]["start"] == 3.5
    assert "La patience" in rendu[-1]["ass"]           # the title is burnt in under the mosque's

    # the end the human chose is kept: no trimming after the last word
    assert rendu[-1]["w"]["end"] == 15.0

    # validation, then publication, as for any clip
    d = c.get(f"/api/v1/clips/{cid}").json()
    assert d["can_validate"] and d["exists"] and d["caption"].startswith("La patience\n")
    assert c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"}).status_code == 200
    r = c.ecrire(f"/clips/{cid}/publish", {"connector": "local", "caption": "x"})
    assert r.status_code == 200, r.text
    assert _clip(conn, cid)["status"] == "publie"

    acts = [a["action"] for a in db.all_(conn, "SELECT action FROM audit WHERE object_type = 'clip' AND object_id = ? "
                                               "ORDER BY id", cid)]
    assert acts[:4] == ["clip.cree", "clip.regeneration", "clip.regenere", "clip.valide"]
    hist = [h["action"] for h in c.get(f"/api/v1/clips/{cid}").json()["history"]]
    assert "clip.cree" in hist
    # the queue shows it with its own title, not the khutba's
    item = next(i for i in c.get("/api/v1/clips").json()["items"] if i["id"] == cid)
    assert item["clip_title"] == "La patience" and item["origin"] == "manuel"


def test_ajustement_des_bornes_d_un_clip_propose(env, rendu):
    conn = env["conn"]
    cid = clip_ids(env)[0]                       # model's clip 0–10 s, "verset"
    c = client(env, "imam")                      # the imam adjusts the clips of his own khutbas
    r = c.ecrire(f"/clips/{cid}/bounds", {"start": "1", "end": "12.5"})
    assert r.status_code == 200, r.text
    clip = _clip(conn, cid)
    assert (clip["start"], clip["end"]) == (1.0, 12.5)
    assert clip["version"] == 2 and clip["needs_regen"] == 0 and clip["status"] == "a_valider"
    assert clip["bounds_by"] == env["ids"]["imam"] and clip["origin"] == "ia"
    assert rendu[-1]["w"]["start"] == 1.0 and rendu[-1]["w"]["end"] == 12.5
    a = db.one(conn, "SELECT * FROM audit WHERE action = 'clip.bornes'")
    assert a["user_id"] == env["ids"]["imam"] and "12.5" in a["details"]

    # a rejected clip can be saved by better bounds: back in the queue
    c.ecrire(f"/clips/{cid}/decision", {"decision": "rejete", "reason": "coupé trop tôt"})
    assert c.ecrire(f"/clips/{cid}/bounds", {"start": "1", "end": "13"}).status_code == 200
    clip = _clip(conn, cid)
    assert clip["status"] == "a_valider" and clip["reject_reason"] == "" and clip["version"] == 3

    # same bounds: nothing to do
    assert c.ecrire(f"/clips/{cid}/bounds", {"start": "1", "end": "13"}).status_code == 422

    # published: frozen
    conn.execute("UPDATE clips SET status = 'publie' WHERE id = ?", (cid,))
    r = c.ecrire(f"/clips/{cid}/bounds", {"start": "2", "end": "13"})
    assert r.status_code == 422 and "published" in r.json()["message"]
    assert _clip(conn, cid)["start"] == 1.0


@pytest.mark.parametrize("corps, motif", [
    ({"start": "10", "end": "5"}, "after the start"),
    ({"start": "-1", "end": "8"}, "before the recording"),
    ({"start": "0", "end": "2"}, "invalid duration"),
    ({"start": "0", "end": "400"}, "invalid duration"),
    ({"start": "8", "end": "30"}, "past the end of the recording"),  # the khutba lasts 20 s
    ({"start": "abc", "end": "8"}, "unreadable"),
    ({"start": "nan", "end": "8"}, "unreadable"),
    ({"start": "0", "end": "8", "kind": "inconnu"}, "unknown clip type"),
])
def test_bornes_invalides_refusees(env, rendu, corps, motif):
    conn, kid = env["conn"], env["kid"]
    c = client(env, "responsable")
    avant = db.one(conn, "SELECT COUNT(*) n FROM clips")["n"]
    r = c.ecrire(f"/sermons/{kid}/clips", corps)
    assert r.status_code == 422 and motif in r.json()["message"]
    assert db.one(conn, "SELECT COUNT(*) n FROM clips")["n"] == avant
    if "kind" not in corps:
        cid = clip_ids(env)[1]
        r = c.ecrire(f"/clips/{cid}/bounds", corps)
        assert r.status_code == 422 and motif in r.json()["message"]
        assert (_clip(conn, cid)["start"], _clip(conn, cid)["end"]) == (12.0, 20.0)
    assert not rendu


def test_permissions_et_source_absente(env, monkeypatch):
    conn, kid = env["conn"], env["kid"]
    # an imam does not create clips on someone else's khutba
    r = client(env, "imam2").ecrire(f"/sermons/{kid}/clips", {"start": "0", "end": "8"})
    assert r.status_code == 404
    r = client(env, "imam2").ecrire(f"/clips/{clip_ids(env)[0]}/bounds", {"start": "0", "end": "8"})
    assert r.status_code == 404
    # without a source video (nor ffmpeg in the test): refused with a reason, nothing created
    c = client(env, "responsable")
    d = c.get(f"/api/v1/sermons/{kid}").json()["manual_clip"]
    assert not d["allowed"] and d["reason"] and "/" not in d["reason"]
    r = c.ecrire(f"/sermons/{kid}/clips", {"start": "0", "end": "8"})
    assert r.status_code == 422 and "cannot be produced" in r.json()["message"]
    assert db.one(conn, "SELECT COUNT(*) n FROM clips")["n"] == 2


def test_echec_du_rendu_laisse_le_clip_a_relancer(env, monkeypatch):
    """ffmpeg fails: the clip exists, in error, and can be regenerated from its sheet."""
    conn, kid = env["conn"], env["kid"]
    monkeypatch.setattr(content, "can_regenerate", lambda clip: (True, ""))

    def casse(*a, **k):
        raise RuntimeError("ffmpeg a échoué")
    monkeypatch.setattr(clipmod, "render_clip", casse)
    c = client(env, "responsable")
    cid = c.ecrire(f"/sermons/{kid}/clips", {"start": "0", "end": "8"}).json()["id"]
    clip = _clip(conn, cid)
    assert clip["regen_state"] == "erreur" and clip["needs_regen"] == 1 and clip["version"] == 0
    assert c.ecrire(f"/clips/{cid}/decision", {"decision": "valide"}).status_code == 422
    monkeypatch.setattr(clipmod, "render_clip", lambda src, off, w, ass, out, log=print, crop=None, fade_out=None:
                        Path(out).write_bytes(b"ok"))
    assert c.ecrire(f"/clips/{cid}/regenerate").status_code == 200
    assert _clip(conn, cid)["version"] == 1


def test_source_du_clip_manuel(env, tmp_path):
    """Same recording as the model's clips; otherwise the live recording; otherwise the khutba video."""
    conn = env["conn"]
    k = db.one(conn, "SELECT * FROM khutbas")
    assert content.clip_source(conn, k)["source_offset"] == 100.0
    conn.execute("UPDATE clips SET video_source = NULL")
    video = tmp_path / "khutba.mp4"
    video.write_bytes(b"v")
    k = dict(k, recording=None, video_path=str(video))
    assert content.clip_source(conn, k) == {"video_source": str(video), "source_offset": 0.0, "crop": None}
