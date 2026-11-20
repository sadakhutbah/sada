"""Telemetry: off by default, and nothing sensitive in what is exported."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sada import notify, telemetry
from sada.backoffice.jobs import Job, JobStore
from sada.pipeline import llm
from sada.state import State

# What must never appear in exported data.
PROMPT = "PROMPT-sermon-text-and-janaza-name-Abdallah"
API_KEY = "sk-SECRET-API-KEY"
QUERY_SECRET = "QUERY-SECRET-TOKEN"
HEADER_SECRET = "HEADER-SECRET-BEARER"
CLIENT_IP = "203.0.113.77"
ANSWER = "ANSWER-private-llm-response"
SECRETS = (PROMPT, API_KEY, QUERY_SECRET, HEADER_SECRET, CLIENT_IP, ANSWER)


class Exported:
    """In-memory exporter and reader behind a started telemetry."""

    def __init__(self):
        self.spans = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.telemetry = telemetry.start("sada-test", "al-nour", span_exporter=self.spans, metric_reader=self.reader)

    def metrics(self) -> dict[str, list[dict]]:
        """Metric name -> data points ({attributes, value | count})."""
        collected = self.reader.get_metrics_data()
        data = json.loads(collected.to_json()) if collected else {"resource_metrics": []}
        points: dict[str, list[dict]] = {}
        for resource in data["resource_metrics"]:
            for scope in resource["scope_metrics"]:
                for metric in scope["metrics"]:
                    points.setdefault(metric["name"], []).extend(metric["data"]["data_points"])
        return points

    def dump(self) -> str:
        """Everything exported, as one text: resource, scope, names, attributes, values."""
        spans = "".join(span.to_json() for span in self.spans.get_finished_spans())
        collected = self.reader.get_metrics_data()
        return spans + (collected.to_json() if collected else "")

    def attribute_keys(self) -> set[str]:
        keys = {k for span in self.spans.get_finished_spans() for k in span.attributes}
        keys |= {k for points in self.metrics().values() for point in points for k in point["attributes"]}
        return keys


@pytest.fixture
def exported():
    out = Exported()
    yield out
    telemetry.stop()


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in (*telemetry.ENDPOINT_ENVS, telemetry.FLAG_ENV, "OTEL_SDK_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    yield
    telemetry.stop()


def points(out: Exported, name: str) -> dict[tuple, float]:
    """{sorted attributes: count} of a counter or histogram."""
    return {tuple(sorted(p["attributes"].items())): p.get("value", p.get("count")) for p in out.metrics().get(name, [])}


# --------------------------------------------------------------------------- off by default


def test_disabled_by_default_creates_no_exporter(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("an exporter or provider was created while telemetry is off")

    monkeypatch.setattr(telemetry, "build", forbidden)
    result = telemetry.start("sada-backoffice")
    assert result is telemetry.get()
    assert not result.enabled
    # every entry point is a harmless no-op
    with result.track("llm", protocol="openai") as run:
        pass
    assert run.status == telemetry.OK
    result.state_changed("IDLE", "ARMED")
    result.watch_orchestrator(lambda: None)
    result.instrument_app(FastAPI())
    result.shutdown()


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, True),
        ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces"}, True),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "  "}, False),
        ({"SADA_TELEMETRY": "1"}, True),
        ({"SADA_TELEMETRY": "0", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, False),
        ({"OTEL_SDK_DISABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, False),
    ],
)
def test_enabled_from_environment(environ, expected):
    assert telemetry.enabled(environ) is expected


def test_start_builds_only_when_enabled(monkeypatch):
    built = []
    monkeypatch.setattr(telemetry, "build", lambda *a, **k: built.append(a) or telemetry.DisabledTelemetry())
    telemetry.start("sada-backoffice")
    assert built == []
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    telemetry.start("sada-orchestrator", "al-nour")
    assert built == [("sada-orchestrator", "al-nour")]


def test_resource_carries_service_and_mosque(exported):
    exported.telemetry.state_changed("IDLE", "ARMED")
    resource = json.loads(exported.reader.get_metrics_data().to_json())["resource_metrics"][0]["resource"]
    assert resource["attributes"]["service.name"] == "sada-test"
    assert resource["attributes"][telemetry.MOSQUE_ATTRIBUTE] == "al-nour"


# --------------------------------------------------------------------------- metrics


def test_llm_call_is_counted_timed_and_traced_without_its_content(exported, monkeypatch):
    monkeypatch.setenv("SADA_LLM_ENABLED", "1")
    monkeypatch.setenv("SADA_LLM_API_KEY", API_KEY)
    answers = iter([{"choices": [{"message": {"content": json.dumps({"summary": ANSWER})}}]}, None])

    def post(url, body, headers, timeout):
        answer = next(answers)
        if answer is None:  # the provider echoes the prompt and the key in its error
            raise RuntimeError(f"bad request {body['messages'][0]['content']} {headers['Authorization']}")
        return answer

    monkeypatch.setattr(llm, "_post", post)
    llm.use(url="http://llm.example:8000/v1?key=" + API_KEY, model="m", protocol="openai")
    try:
        assert llm.ask_json(PROMPT, 5, log=lambda m: None) == {"summary": ANSWER}
        assert llm.ask_json(PROMPT, 5, log=lambda m: None) is None
    finally:
        llm.use()

    assert points(exported, "sada.llm.calls") == {
        (("protocol", "openai"), ("status", "ok")): 1,
        (("protocol", "openai"), ("status", "error")): 1,
    }
    assert points(exported, "sada.llm.duration")
    spans = [s for s in exported.spans.get_finished_spans() if s.name == "sada.llm"]
    assert [s.attributes["status"] for s in spans] == ["ok", "error"]
    assert spans[1].attributes["error.type"] == "RuntimeError"
    assert spans[1].events == ()
    assert spans[1].status.description is None

    dump = exported.dump()
    for secret in SECRETS:
        assert secret not in dump, secret
    assert exported.attribute_keys() <= telemetry.METRIC_ATTRIBUTES | telemetry.ALLOWED_ATTRIBUTES


def test_exporter_drops_everything_outside_the_allowlist(exported):
    tracer = exported.telemetry.tracer_provider.get_tracer("hostile")
    with tracer.start_as_current_span("GET /items/{i}") as span:
        span.set_attribute("http.url", f"https://sada.example/items/1?token={QUERY_SECRET}")
        span.set_attribute("http.target", f"/items/1?token={QUERY_SECRET}")
        span.set_attribute("client.address", CLIENT_IP)
        span.set_attribute("http.request.header.authorization", HEADER_SECRET)
        span.set_attribute("http.route", "/items/{i}")
        span.record_exception(ValueError(f"leaked {PROMPT}"))
        span.set_status(telemetry_status_error(f"failed on {PROMPT}"))
    (exported_span,) = exported.spans.get_finished_spans()
    assert dict(exported_span.attributes) == {"http.route": "/items/{i}"}
    assert exported_span.events == ()
    assert exported_span.status.description is None
    for secret in (PROMPT, QUERY_SECRET, CLIENT_IP, HEADER_SECRET):
        assert secret not in exported.dump()


def telemetry_status_error(description: str):
    from opentelemetry.trace import Status, StatusCode

    return Status(StatusCode.ERROR, description)


def test_notifications_by_channel(exported):
    sent = notify.Notifier(webhook="https://hooks.example/" + API_KEY, post=lambda url, payload: None)
    assert sent.send(PROMPT)

    def broken(url, payload):
        raise OSError(f"cannot reach {url} with {payload}")

    failed = notify.Notifier(telegram_token=API_KEY, telegram_chat="42", post=broken, log=lambda m: None)
    assert not failed.send(PROMPT)
    notify.Notifier(log=lambda m: None).send(PROMPT)  # dry run: nothing is sent, nothing counted

    assert points(exported, "sada.notification.sent") == {
        (("channel", "webhook"), ("status", "ok")): 1,
        (("channel", "telegram"), ("status", "error")): 1,
    }
    for secret in (PROMPT, API_KEY):
        assert secret not in exported.dump()


class MemoryStore(JobStore):
    def __init__(self):
        self.rows: dict = {}

    def read(self, conn, ref):
        return self.rows.get(ref["id"], {})

    def write(self, conn, ref, state, error, extra):
        self.rows[ref["id"]] = {"status": state.value if state else None, "error": error}


def test_jobs_by_kind_and_status(exported):
    job = Job(MemoryStore(), busy_message=lambda: "busy", thread_name="render")

    def finishes(ref):
        job.mark_ready(None, ref)

    def reports_failure(ref):
        job.mark_failed(None, ref, f"boom {PROMPT}")

    def raises(ref):
        raise ValueError(PROMPT)

    for n, target in enumerate((finishes, reports_failure, raises), start=1):
        ref = {"id": n}
        if target is raises:
            with pytest.raises(ValueError):
                job.start(None, ref, target, (ref,), background=False)
        else:
            job.start(None, ref, target, (ref,), background=False)

    assert points(exported, "sada.job.runs") == {
        (("kind", "render"), ("status", "ok")): 1,
        (("kind", "render"), ("status", "error")): 2,
    }
    assert not job._outcomes
    assert PROMPT not in exported.dump()


def test_back_office_runs_are_labelled_with_their_mosque(exported):
    """One back-office serves every hosted mosque: a job, and what it tracks itself, carry its mosque."""
    job = Job(MemoryStore(), busy_message=lambda: "busy", thread_name="soustitres")

    def translates(ref):
        with telemetry.get().track("llm", protocol="openai"):
            pass
        job.mark_ready(None, ref)

    job.start(None, {"id": 1}, translates, ({"id": 1},), background=False, mosque="al-nour")
    job.start(None, {"id": 2}, translates, ({"id": 2},), background=False)  # mosque unknown: no label

    assert points(exported, "sada.job.runs") == {
        (("kind", "soustitres"), ("mosque", "al-nour"), ("status", "ok")): 1,
        (("kind", "soustitres"), ("status", "ok")): 1,
    }
    assert points(exported, "sada.llm.calls") == {
        (("mosque", "al-nour"), ("protocol", "openai"), ("status", "ok")): 1,
        (("protocol", "openai"), ("status", "ok")): 1,
    }


def test_mosque_scope_is_restored_and_an_explicit_label_wins(exported):
    with telemetry.mosque_scope("a"):
        with telemetry.mosque_scope(""):  # empty: keeps the enclosing mosque
            with telemetry.get().track("notification", channel="ntfy"):
                pass
        with telemetry.get().track("notification", channel="ntfy", mosque="b"):
            pass
    with telemetry.get().track("notification", channel="ntfy"):
        pass

    assert points(exported, "sada.notification.sent") == {
        (("channel", "ntfy"), ("mosque", "a"), ("status", "ok")): 1,
        (("channel", "ntfy"), ("mosque", "b"), ("status", "ok")): 1,
        (("channel", "ntfy"), ("status", "ok")): 1,
    }


def test_state_transitions_and_orchestrator_gauges(exported):
    exported.telemetry.state_changed("IDLE", "ARMED")
    exported.telemetry.state_changed("ARMED", "BEFORE")
    snapshot = telemetry.OrchestratorSnapshot(
        state="KHUTBA",
        stream_on=True,
        stream_live_s=125.5,
        encoder_alive=True,
        encoder_restarts=2,
        ingest_connected=False,
    )
    exported.telemetry.watch_orchestrator(lambda: snapshot)

    assert points(exported, "sada.state.transitions") == {
        (("from_state", "ARMED"), ("to_state", "BEFORE")): 1,
        (("from_state", "IDLE"), ("to_state", "ARMED")): 1,
    }
    states = {dict(k)["state"]: v for k, v in points(exported, "sada.orchestrator.state").items()}
    assert states == {state.value: int(state is State.KHUTBA) for state in State}
    for name, value in {
        "sada.stream.on": 1,
        "sada.stream.live.duration": 125.5,
        "sada.encoder.up": 1,
        "sada.encoder.restarts": 2,
        "sada.ingest.connected": 0,
    }.items():
        assert points(exported, name) == {(): value}, name


# --------------------------------------------------------------------------- HTTP


def make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item}")
    def item(item: str):
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_http_spans_and_metrics_keep_the_route_only(exported):
    app = make_app()
    exported.telemetry.instrument_app(app)
    client = TestClient(app, client=(CLIENT_IP, 50000))
    headers = {"Authorization": f"Bearer {HEADER_SECRET}", "Cookie": f"session={HEADER_SECRET}"}
    assert client.get(f"/items/{PROMPT}?token={QUERY_SECRET}", headers=headers).status_code == 200
    assert client.get(f"/unknown/{PROMPT}?token={QUERY_SECRET}", headers=headers).status_code == 404
    assert client.get("/health").status_code == 200

    names = sorted(span.name for span in exported.spans.get_finished_spans())
    assert names == ["GET", "GET /items/{item}"]  # no /health, no internal send/receive spans
    routed = next(s for s in exported.spans.get_finished_spans() if s.name == "GET /items/{item}")
    assert dict(routed.attributes) == {"http.method": "GET", "http.route": "/items/{item}", "http.status_code": 200}
    assert exported.metrics().get("http.server.duration")

    dump = exported.dump()
    for secret in (PROMPT, QUERY_SECRET, HEADER_SECRET, CLIENT_IP, "/health", "testserver"):
        assert secret not in dump, secret


def otlp_bytes(out: Exported) -> bytes:
    """What the OTLP exporters would put on the wire (protobuf), resource and exemplars included."""
    from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

    spans = encode_spans(out.spans.get_finished_spans()).SerializeToString()
    return spans + encode_metrics(out.reader.get_metrics_data()).SerializeToString()


def test_nothing_leaks_through_the_resource_exemplars_or_inbound_context(exported, monkeypatch):
    secret_pw, host_secret, forwarded = "SECRETPW", "internal-host-SECRET.example", "198.51.100.99"
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"db.password={secret_pw}")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "SECRET-SERVICE")
    out = Exported()  # started after the environment is set
    app = make_app()
    out.telemetry.instrument_app(app)
    client = TestClient(app, client=(CLIENT_IP, 50000))
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    headers = {
        "Host": host_secret,
        "Authorization": f"Bearer {HEADER_SECRET}",
        "X-Forwarded-For": forwarded,
        "traceparent": f"00-{trace_id}-b7ad6b7169203331-01",
        "tracestate": "vendor=TRACESTATE-SECRET",
        "baggage": "user=BAGGAGE-SECRET",
    }
    for path in (f"/items/{PROMPT}?token={QUERY_SECRET}", f"/unknown/{PROMPT}?token={QUERY_SECRET}"):
        client.get(path, headers=headers)
    with out.telemetry.track("job", kind="x"):
        pass

    spans = out.spans.get_finished_spans()
    assert sorted(s.name for s in spans) == ["GET", "GET /items/{item}", "sada.job"]
    for span in spans:
        assert format(span.context.trace_id, "032x") != trace_id  # fresh root, caller's id not adopted
        assert span.parent is None
        assert not span.context.trace_state
    assert {k for s in spans for k in s.resource.attributes} == {"service.name", telemetry.MOSQUE_ATTRIBUTE}

    wire = otlp_bytes(out) + out.dump().encode()
    forbidden = (
        secret_pw, "db.password", "SECRET-SERVICE", PROMPT, QUERY_SECRET, HEADER_SECRET, CLIENT_IP, host_secret,
        forwarded, trace_id, "TRACESTATE-SECRET", "BAGGAGE-SECRET", "vendor=", "/health",
    )  # fmt: skip
    for secret in forbidden:
        assert secret.encode() not in wire, secret
    telemetry.stop()


@pytest.mark.parametrize(
    ("endpoint", "warns"),
    [
        ("https://collector.tail1234.ts.net:4318", False),
        ("https://100.101.102.103:4318", False),
        ("https://10.0.0.5:4318", False),
        ("https://localhost:4318", False),
        ("http://127.0.0.1:4318", True),  # not https
        ("https://otel.example.com:4318", True),  # public
        ("http://8.8.8.8:4318", True),
    ],
)
def test_warns_on_public_or_clear_text_endpoint_without_logging_secrets(endpoint, warns, caplog):
    caplog.set_level("WARNING", logger="sada.telemetry")
    secret_url = endpoint.replace("://", "://user:URLSECRET@") + "/v1/traces?key=URLSECRET"
    result = telemetry.warn_unsafe_endpoints({"OTEL_EXPORTER_OTLP_ENDPOINT": secret_url})
    assert bool(result) is warns
    assert bool(caplog.records) is warns
    assert "URLSECRET" not in caplog.text


def test_job_outcomes_are_guarded_by_the_lock():
    job = Job(MemoryStore(), busy_message=lambda: "busy")
    job._lock.acquire()
    try:
        done = threading.Event()
        threading.Thread(target=lambda: (job.mark_ready(None, {"id": 1}), done.set()), daemon=True).start()
        assert not done.wait(0.2)  # blocked on the lock
    finally:
        job._lock.release()
    assert done.wait(2)
