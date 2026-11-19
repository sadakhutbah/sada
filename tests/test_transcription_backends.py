"""Transcription backend registry and engine conformance."""

from pathlib import Path

from sada.pipeline import remote, repair, transcribe


def test_engines_declare_the_protocol():
    assert repair.Engine in transcribe.LocalEngine.__mro__
    assert repair.Engine in remote.RemoteEngine.__mro__
    local = transcribe.LocalEngine(model=None, audio=[])
    rem = remote.RemoteEngine(remote.settings(), "x.wav", "", print, Path("."))
    assert isinstance(local, repair.Engine)
    assert isinstance(rem, repair.Engine)


def test_registry_covers_every_mode():
    assert set(transcribe.BACKENDS) == set(remote.MODES)


def test_backend_name_follows_remote_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("SADA_TRANSCRIPTION_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.delenv("SADA_TRANSCRIPTION_MODE", raising=False)
    assert transcribe.backend_name() == "local"
    monkeypatch.setenv("SADA_TRANSCRIPTION_MODE", "distant")
    assert transcribe.backend_name() == "distant"


def test_transcribe_dispatches_to_remote(monkeypatch):
    seen = {}

    def fake(wav, out_json, **kw):
        seen.update(wav=wav, out=out_json, **kw)
        return {"ok": True}

    monkeypatch.setenv("SADA_TRANSCRIPTION_MODE", "distant")
    monkeypatch.setattr(remote, "transcribe", fake)
    res = transcribe.transcribe("a.wav", "a.json", prefer="small", hotwords="h", source={"k": 1})
    assert res == {"ok": True}
    assert seen["hotwords"] == "h" and seen["source"] == {"k": 1} and seen["arabic_retry"] is None
    assert "prefer" not in seen
