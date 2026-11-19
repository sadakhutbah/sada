"""Export of the transcript (original + translation, timestamped) as .txt / .srt."""

from __future__ import annotations

import re

from conftest import client

from sada.backoffice import transcript_export as te

ROWS = [
    {"start": 3725.5, "end": 3730.0, "text": "الصبر نور", "fr": "La patience est lumière", "verse": None},
    {"start": 1.0, "end": 2.25, "text": "Bismillah", "fr": "Bismillah", "verse": None},
    {"start": 5.0, "end": 6.0, "text": "", "fr": "x", "verse": None},
    {"start": 7.0, "end": 8.0, "text": "تلاوة", "fr": None, "verse": {"fr": "Verset traduit"}},
]


def test_entries_sorted_skip_empty_and_dedupe_translation():
    items = te.entries(ROWS)
    assert [e["text"] for e in items] == ["Bismillah", "تلاوة", "الصبر نور"]
    assert items[0]["translation"] == ""  # same as the original
    assert items[1]["translation"] == "Verset traduit"


def test_srt_format():
    out = te.to_srt(te.entries(ROWS))
    blocks = out.strip().split("\n\n")
    assert len(blocks) == 3
    assert blocks[0] == "1\n00:00:01,000 --> 00:00:02,250\nBismillah"
    assert blocks[2] == "3\n01:02:05,500 --> 01:02:10,000\nالصبر نور\nLa patience est lumière"
    for b in blocks:
        lines = b.split("\n")
        assert lines[0].isdigit()
        assert re.fullmatch(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", lines[1])


def test_txt_format():
    out = te.to_txt(te.entries(ROWS))
    assert out.splitlines()[0] == "[00:00:01] Bismillah"
    assert "[01:02:05] الصبر نور\n           La patience est lumière" in out
    assert te.to_txt([]) == ""


def test_api_download_srt_and_txt(env):
    c = client(env, "imam")
    base = f"/api/v1/sermons/{env['kid']}/transcript"
    r = c.get(base + "?format=srt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-subrip")
    assert "attachment" in r.headers["content-disposition"]
    texte = r.content.decode("utf-8")
    assert re.match(r"1\n\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}\n", texte)
    assert "la patience" in texte

    t = c.get(base + "?format=txt")
    assert t.status_code == 200
    assert t.headers["content-type"].startswith("text/plain")
    assert re.match(r"\[\d\d:\d\d:\d\d\] ", t.text)
    assert c.get(base).status_code == 200  # txt by default


def test_api_transcript_errors_and_auth(env):
    base = f"/api/v1/sermons/{env['kid']}/transcript"
    assert client(env).get(base + "?format=srt").status_code == 401
    c = client(env, "imam")
    assert c.get(base + "?format=pdf").status_code == 422
    assert c.get("/api/v1/sermons/999999/transcript?format=srt").status_code == 404
