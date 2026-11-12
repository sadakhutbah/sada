def test_le_resume_ne_demande_plus_les_extraits(monkeypatch):
    """Le résumé ne porte plus les bornes des clips : la sélection est faite à part (select.py)."""
    from sada.pipeline import notes as N

    vu = []
    monkeypatch.setattr(N.llm, "ask_json", lambda prompt, timeout, log: vu.append(prompt) or {"points": []})
    segs = [{"start": 0.0, "end": 6.0, "text": "Bismillah, mes freres."}]

    assert N.llm_summary(segs, []) is not None
    assert '"clips"' not in vu[0]
    assert '"points"' in vu[0] and '"citations"' in vu[0]

    _, extra = N.build_notes("essai", segs, [], {"duration": 6.0}, use_llm=True)
    assert "clip_windows" not in extra


def test_transcription_allegee_sans_arabe_ni_phonetique():
    """Lignes arabes retirées, suites phonétiques retirées, la traduction française reste."""
    from sada.pipeline.notes import lighten_segments

    segs = [
        {"start": 0.0, "end": 4.0, "text": "أيها المسلمون اتقوا الله"},
        {"start": 4.0, "end": 8.0, "text": "qala anna inna qawluhu, il oublie."},
        {"start": 8.0, "end": 12.0, "text": "Une seconde phrase claire du discours."},
    ]
    out = lighten_segments(segs)
    assert out == ["[00:04] il oublie.", "[00:08] Une seconde phrase claire du discours."], out
