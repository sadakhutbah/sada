"""The post-live pipeline runs in its own process: it exports its telemetry labelled with its mosque."""

from sada import telemetry
from sada.backoffice import tenant_config, traitement
from sada.pipeline import run


def test_back_office_passes_the_mosque_to_the_pipeline(monkeypatch):
    monkeypatch.setattr(tenant_config, "pipeline_env", lambda mosque: {})
    assert traitement._env({"slug": "al-nour"})["SADA_MOSQUE_SLUG"] == "al-nour"


def test_pipeline_starts_its_telemetry_with_the_mosque_and_flushes_it(monkeypatch):
    calls = []
    monkeypatch.setenv("SADA_MOSQUE_SLUG", "al-nour")
    monkeypatch.setattr(telemetry, "start", lambda service, mosque="": calls.append(("start", service, mosque)))
    monkeypatch.setattr(telemetry, "stop", lambda: calls.append(("stop",)))
    monkeypatch.setattr(run, "_main", lambda argv=None: calls.append(("run", argv)) or 0)

    assert run.main(["x"]) == 0
    assert calls == [("start", "sada-pipeline", "al-nour"), ("run", ["x"]), ("stop",)]


def test_pipeline_telemetry_is_flushed_when_the_run_fails(monkeypatch):
    stopped = []
    monkeypatch.setattr(telemetry, "start", lambda *a, **k: None)
    monkeypatch.setattr(telemetry, "stop", lambda: stopped.append(True))

    def boom(argv=None):
        raise RuntimeError("x")

    monkeypatch.setattr(run, "_main", boom)
    try:
        run.main([])
    except RuntimeError:
        pass
    assert stopped == [True]
