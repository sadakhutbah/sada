import wave

from sada.pipeline.run import transcript_key, transcript_matches


def _wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return str(path)


def test_same_range_reuses_cache(tmp_path):
    key = transcript_key("rec.mkv", 0, 540)
    assert transcript_matches({"source": key, "duration": 540}, key, _wav(tmp_path / "a.wav", 1))


def test_other_range_is_retranscribed(tmp_path):
    old = transcript_key("rec.mkv", 0, 540)
    new = transcript_key("rec.mkv", 100, 540)
    assert not transcript_matches({"source": old}, new, _wav(tmp_path / "a.wav", 1))


def test_legacy_cache_checked_on_audio_length(tmp_path):
    key = transcript_key("rec.mkv", 100, 540)
    wav = _wav(tmp_path / "a.wav", 4)
    assert transcript_matches({"duration": 4.0}, key, wav)
    assert not transcript_matches({"duration": 9.0}, key, wav)


def test_session_header_follows_kind_and_mosque():
    from types import SimpleNamespace

    from sada.pipeline import clips as clipmod
    from sada.pipeline.run import session_header

    a = SimpleNamespace(title=clipmod.DEFAULT_TITLE, subtitle="")
    data = {"mosque": "Mosquée Exemple"}
    assert session_header({"kind": "khutba", "title": ""}, data, a) == {
        "title": clipmod.DEFAULT_TITLE,
        "subtitle": "Mosquée Exemple",
    }
    assert session_header({"kind": "dars", "title": "Explication"}, data, a)["title"] == "Explication"
    assert session_header({"kind": "conference", "title": ""}, data, a)["title"] == "Conférence"
    assert session_header({"kind": "dars"}, data, SimpleNamespace(title="Forcé", subtitle="x")) == {
        "title": "Forcé",
        "subtitle": "x",
    }


def test_dominant_language_over_whole_talk():
    from sada.pipeline.transcribe import dominant_language

    def seg(s, e, t):
        return {"start": s, "end": e, "text": t}

    talk = [seg(0, 30, "بسم الله الرحمن الرحيم"), seg(30, 600, "Chers frères, aujourd'hui nous parlons de la tawba")]
    assert dominant_language(talk, "ar") == "fr"
    assert dominant_language([seg(0, 600, "الحمد لله رب العالمين")], "ar") == "ar"
    assert dominant_language([], "ar") == "ar"


def test_small_model_not_picked_by_default(monkeypatch):
    from sada.pipeline import transcribe as tr

    monkeypatch.setattr(tr, "free_vram_mb", lambda: 1900)
    assert tr.pick_model(None, log=lambda *_: None) == ("large-v3-turbo", "cpu", "int8")
    monkeypatch.setattr(tr, "free_vram_mb", lambda: 3000)
    assert tr.pick_model(None, log=lambda *_: None)[1] == "cuda"
