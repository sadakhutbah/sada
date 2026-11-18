"""Search: Arabic normalisation, verse references, word / verse / semantic hits, scope, post-live
embeddings (waiting for the lives to end), and graceful degradation without the model."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from conftest import client, count, one, update

from sada.backoffice import embeddings, models, search, traitement


def test_arabic_is_folded_the_same_way_for_text_and_query():
    assert search.normalize("الصَّبْرُ") == search.normalize("الصبر") == "صبر"
    assert search.normalize("رحمة") == search.normalize("رحمه")  # ta marbuta
    assert search.normalize("إِلى") == search.normalize("الي") or search.normalize("إلى") == "الي"
    assert search.normalize("أحمد") == search.normalize("احمد")
    assert search.normalize("صـــبر") == "صبر"  # tatweel
    assert search.normalize("الله") == "الله"  # too short to lose its article
    assert search.terms("Épreuves  de la VIE") == ["epreuves", "de", "la", "vie"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2:153", (2, 153, 153)),
        ("2:153-155", (2, 153, 155)),
        ("٢:١٥٣", (2, 153, 153)),
        ("Al-Baqara", (2, None, None)),
        ("al baqarah 201", (2, 201, 201)),
        ("البقرة 201", (2, 201, 201)),
        ("سورة الكهف", (18, None, None)),
        ("sourate la vache", (2, None, None)),
        ("Ya-Sin", (36, None, None)),
        ("surah Al-Kahf", (18, None, None)),
        ("The Cow", (2, None, None)),
        ("2:999", None),
        ("115:1", None),
        ("patience", None),
        ("la", None),
    ],
)
def test_verse_queries(query, expected):
    assert search.parse_verse(query) == expected


def test_import_fills_the_index(env):
    conn = env["conn"]
    assert count(conn, models.TranscriptSegment, sermon_id=env["kid"]) == 4
    assert one(conn, models.VerseCitation, sermon_id=env["kid"])["surah"] == 65
    # the Arabic segment of a session without detected language is indexed as Arabic
    seg = one(conn, models.TranscriptSegment, sermon_id=env["kid"], seg_index=2)
    assert seg["lang"] == "ar" and seg["norm"] == search.normalize(seg["text"])
    # unchanged output: the next scan does not rewrite the rows
    k = one(conn, models.Khutba, id=env["kid"])
    assert search.index_sermon(conn, k) == 0


def test_backfill_rebuilds_a_missing_index(env):
    conn = env["conn"]
    conn.execute(search.TS.delete())
    conn.execute(search.VC.delete())
    conn.commit()
    assert search.index_all(conn, log=lambda *a: None) == (1, 0)
    assert search.index_all(conn, log=lambda *a: None) == (0, 1)
    assert count(conn, models.TranscriptSegment, sermon_id=env["kid"]) == 4


def test_word_search_api(env):
    c = client(env, "responsable")
    r = c.get("/api/v1/search", params={"q": "patience"})
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x["id"] for x in res] == [env["kid"]]
    hits = res[0]["hits"]
    assert {h["type"] for h in hits} == {"word"}
    assert [h["start"] for h in hits] == sorted(h["start"] for h in hits)
    assert any(p["hit"] and p["text"].lower() == "patience" for p in hits[0]["snippet"])
    assert res[0]["kind"] == "khutba"


def test_vowelled_arabic_query_finds_the_plain_transcript(env):
    c = client(env, "responsable")
    res = c.get("/api/v1/search", params={"q": "يَتَوَكَّلْ"}).json()["results"]
    assert res and res[0]["hits"][0]["start"] == 9.0


def test_verse_search_api(env):
    c = client(env, "responsable")
    for q in ("65:3", "At-Talaq", "الطلاق", "sourate le divorce 3"):
        res = c.get("/api/v1/search", params={"q": q}).json()["results"]
        assert res, q
        h = res[0]["hits"][0]
        assert h["type"] == "verse" and h["start"] == 9.0 and h["verse"]["surah"] == 65 and h["verse"]["ayah"] == 3
    assert c.get("/api/v1/search", params={"q": "65:4"}).json()["results"] == []


def test_search_is_scoped(env):
    from sada.backoffice import db

    # another imam of the mosque without history_all: not his sermon
    assert client(env, "imam2").get("/api/v1/search", params={"q": "patience"}).json()["results"] == []
    assert client(env, "imam").get("/api/v1/search", params={"q": "patience"}).json()["results"]
    # another mosque: its own sermons only
    other = db.create_mosque(env["conn"], "autre", "Autre Mosquée")
    update(env["conn"], models.Khutba, {"mosque_id": other}, id=env["kid"])
    assert client(env, "responsable").get("/api/v1/search", params={"q": "patience"}).json()["results"] == []
    assert client(env).get("/api/v1/search", params={"q": "patience"}).status_code == 401


class FakeModel:
    """Bag-of-words vectors: two texts sharing words are close (stands in for e5 in tests)."""

    def encode(self, texts, kind="passage"):
        out = np.zeros((len(texts), embeddings.DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in search.terms(t):
                out[i, int(hashlib.sha1(w.encode()).hexdigest(), 16) % embeddings.DIM] += 1
            out[i, 0] += 0.01
            out[i] /= np.linalg.norm(out[i])
        return out


def test_embeddings_wait_for_the_live_then_feed_semantic_search(env, monkeypatch):
    monkeypatch.setenv(embeddings.DISABLE_ENV, "1")
    conn = env["conn"]
    monkeypatch.setattr(embeddings, "installed", lambda: True)
    monkeypatch.setattr(embeddings, "get", lambda: FakeModel())
    monkeypatch.setattr(search, "SEMANTIC_MIN", 0.3)
    assert search.pending_embeddings(conn) == 4
    assert traitement.embed_pending(conn, log=lambda *a: None, is_live=lambda: True) == 0
    assert search.pending_embeddings(conn) == 4
    assert traitement.embed_pending(conn, log=lambda *a: None, is_live=lambda: False) == 4
    assert search.pending_embeddings(conn) == 0
    k = models.Khutba
    res = search.search(conn, [k.mosque_id == env["mid"]], "lumière patience accorde")
    assert res["semantic"] is True
    assert any(h["type"] == "semantic" for r in res["results"] for h in r["hits"])


def test_a_live_starting_stops_the_embeddings(env, monkeypatch):
    monkeypatch.setenv(embeddings.DISABLE_ENV, "1")
    calls = iter([False, True])
    monkeypatch.setattr(embeddings, "installed", lambda: True)
    monkeypatch.setattr(embeddings, "get", lambda: FakeModel())
    assert traitement.embed_pending(env["conn"], log=lambda *a: None, is_live=lambda: next(calls)) == 0
    assert search.pending_embeddings(env["conn"]) == 4


def test_without_model_search_keeps_words_and_verses(env, monkeypatch):
    monkeypatch.setattr(embeddings, "installed", lambda: False)
    monkeypatch.setattr(embeddings, "download", lambda log=print: False)
    assert traitement.embed_pending(env["conn"], log=lambda *a: None, is_live=lambda: False) == 0
    monkeypatch.setenv(embeddings.DISABLE_ENV, "1")
    r = client(env, "responsable").get("/api/v1/search", params={"q": "patience"}).json()
    assert r["semantic"] is False and r["results"]


def test_any_live_reads_the_orchestrator_state(env, monkeypatch):
    from sada.backoffice import live

    monkeypatch.setattr(live, "status_cached", lambda m: {"ok": True, "state": "KHUTBA"})
    assert traitement.any_live(env["conn"]) is True
    monkeypatch.setattr(live, "status_cached", lambda m: {"ok": True, "state": "IDLE"})
    assert traitement.any_live(env["conn"]) is False
    monkeypatch.setattr(live, "status_cached", lambda m: {"ok": False})
    assert traitement.any_live(env["conn"]) is False


def test_highlight_cuts_around_the_first_hit():
    text = "mot " * 100 + "patience " + "fin " * 100
    parts = search.highlight(text, search.terms("patience"))
    assert parts[0]["text"] == "… " and parts[-1]["text"] == " …"
    assert [p["text"] for p in parts if p["hit"]] == ["patience"]


def test_transcription_loops_and_display_language():
    assert search.degenerate("shukr, " * 30)
    assert not search.degenerate("la patience est la clé de la délivrance, disait le Prophète à ses compagnons")
    assert search.text_lang("الصبر مفتاح الفرج", "fr") == "ar"
    assert search.text_lang("la patience est la clé", "ar") == ""
    assert search.text_lang("la patience est la clé", "fr") == "fr"
