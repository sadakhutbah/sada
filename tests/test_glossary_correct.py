from sada.pipeline import glossary, subtitles


def test_correct():
    assert glossary.correct("sur sa faitana, il reste") == "sur sa fitra, il reste"
    assert glossary.correct("Faitana et la dunya") == "Fitra et la dunya"
    assert glossary.correct("la fitra") == "la fitra"


def test_srt_corrige():
    p = subtitles.plan([{"start": 0.0, "end": 3.0, "text": "sur sa faitana, l 'explication de al -lamia"}], [], {"seg": {}, "verse_fr": {}})
    out = subtitles.srt(subtitles.cues(p["passages"]["fr"]))
    assert "fitra" in out and "l'explication" in out and "al-lamia" in out, out
