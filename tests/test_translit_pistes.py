"""L'arabe phonétique (lettres latines) se traduit dans la piste française, jamais recopié brut."""

from sada.pipeline import subtitles
from sada.pipeline.translate import needs_fr
from sada.text import has_translit


def _seg(text, start=0.0):
    return {"start": start, "end": start + 2.0, "text": text}


def test_detection():
    assert has_translit("dhahabu 'l-ladda, wa baqawu 'l-ithmi,")
    assert has_translit("walha an alati lahwin atarabat, détends-toi de la musique")
    assert not has_translit("la dunya est une épreuve, d'accord ?")
    assert not has_translit("ألحمد لله")


def test_a_traduire():
    assert needs_fr("dhahabu 'l-ladda, wa baqawu 'l-ithmi,")
    assert needs_fr("الحمد لله")
    assert not needs_fr("shukr, shukr, shukr, shukr, shukr, shukr")
    assert not needs_fr("on poursuit l'explication de ce poème")


def test_piste_fr():
    segs = [_seg("dhahabu 'l-ladda, wa baqawu 'l-ithmi,"), _seg("al-kufri, al-kufri, al-kufri, al-kufri, al-kufri", 3), _seg("donc on continue", 6)]
    p = subtitles.plan(segs, [], {"seg": {}, "verse_fr": {}})
    assert [x["text"] for x in p["passages"]["fr"]] == ["donc on continue"]
    assert not p["languages"]["fr"]["complete"]
    from sada.pipeline.translate import _seg_key
    tr = {"seg": {_seg_key(segs[0]["text"]): {"fr": "Le plaisir s'en va, le péché demeure.", "coran": False}}, "verse_fr": {}}
    p = subtitles.plan(segs, [], tr)
    assert [x["text"] for x in p["passages"]["fr"]] == ["Le plaisir s'en va, le péché demeure.", "donc on continue"]
    assert p["languages"]["fr"]["complete"]
