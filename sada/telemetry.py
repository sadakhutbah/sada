"""OpenTelemetry metrics and traces, opt-in and privacy-first.

Off by default: nothing is imported from the SDK, no exporter is created, no socket is opened and the
instrumented code paths run against `DisabledTelemetry` (a null object). It is switched on by the standard
OTLP variables, read by the SDK itself (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`,
`OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_RESOURCE_ATTRIBUTES`, ...), or by `SADA_TELEMETRY=1` (default local
endpoint). `SADA_TELEMETRY=0` / `OTEL_SDK_DISABLED=true` force it off.

Each process calls `start()` once at boot with its service name (`sada-backoffice`, `sada-orchestrator`);
the instrumented code only talks to `get()`:

    with telemetry.get().track("llm", protocol="openai"):   # counter + duration histogram + span
        ...

Nothing sensitive leaves the process. Only metrics and traces are exported (never logs), and every exported
datum goes through an allowlist (`ALLOWED_ATTRIBUTES`): spans are rebuilt without events, links, exception
messages or status descriptions, metric attributes are filtered by an SDK `View`. Request and response
bodies and headers are never captured, only the route template of a request is kept (no path, no query
string, no client address) and `/health` is not traced. Attribute values are low-cardinality labels.
"""

from __future__ import annotations

import functools
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

# Any of these switches the export on (the SDK exporters read them again for the actual address).
ENDPOINT_ENVS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)
FLAG_ENV = "SADA_TELEMETRY"
TRUE = ("1", "true", "yes", "on")
FALSE = ("0", "false", "no", "off")

OK, ERROR = "ok", "error"

#: Resource attribute carrying the mosque slug (one orchestrator per mosque).
MOSQUE_ATTRIBUTE = "sada.mosque"

#: The only attribute keys allowed to leave the process, on spans and (with `METRIC_ATTRIBUTES`) on metrics. Anything
#: else added by an instrumentation (URL, path, query, client address, user agent, headers, exception
#: text...) is dropped before export.
ALLOWED_ATTRIBUTES = frozenset(
    {
        # HTTP server (route template only)
        "http.method",
        "http.request.method",
        "http.route",
        "http.status_code",
        "http.response.status_code",
        "error.type",  # exception class name, never its message
        # labels of the metrics below
        "state",
        "from_state",
        "to_state",
        "kind",
        "status",
        "protocol",
        "engine",
        "stage",
        "channel",
    }
)

#: Metric data points may also carry `http.target`: the HTTP instrumentation fills it with the route
#: template (`/items/{i}`), never the raw path, and leaves it out of unmatched requests.
METRIC_ATTRIBUTES = ALLOWED_ATTRIBUTES | {"http.target"}

#: Not traced: probes and static files.
EXCLUDED_URLS = (r"/health(\?|$)", r"/metrics(\?|$)", r"/static/")

#: Operations observed by `Telemetry.track`: key -> (what is counted, description).
OPERATIONS = {
    "llm": ("calls", "Language-model calls"),
    "transcription": ("calls", "Transcription calls"),
    "job": ("runs", "Back-office background jobs"),
    "notification": ("sent", "Notifications to the imam"),
}
DURATION_BUCKETS_S = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600)


class Tracked:
    """Outcome of one tracked operation: `ok` unless the block raised or called `fail()`."""

    def __init__(self) -> None:
        self.status = OK

    def fail(self) -> None:
        self.status = ERROR


@dataclass(frozen=True)
class OrchestratorSnapshot:
    """What the orchestrator reports about itself, read each time metrics are collected."""

    state: str
    stream_on: bool
    stream_live_s: float  # seconds since the stream started, 0 when it is off
    encoder_alive: bool
    encoder_restarts: int
    ingest_connected: bool  # the audio probe on the video input is connected


class DisabledTelemetry:
    """Default: every call is a no-op (null object), nothing is created or sent."""

    enabled = False

    def track(self, operation: str, **attributes: str) -> AbstractContextManager[Tracked]:
        return nullcontext(Tracked())

    def state_changed(self, old: str, new: str) -> None:
        pass

    def watch_orchestrator(self, snapshot: Callable[[], OrchestratorSnapshot]) -> None:
        pass

    def instrument_app(self, app) -> None:
        pass

    def shutdown(self) -> None:
        pass


class Telemetry(DisabledTelemetry):
    """Live telemetry: instruments on its own providers (never the process-global ones)."""

    enabled = True

    def __init__(self, meter_provider, tracer_provider) -> None:
        self.meter_provider, self.tracer_provider = meter_provider, tracer_provider
        self._meter = meter_provider.get_meter("sada")
        self._tracer = tracer_provider.get_tracer("sada")
        self._calls: dict[str, Any] = {}
        self._durations: dict[str, Any] = {}
        for key, (noun, description) in OPERATIONS.items():
            self._calls[key] = self._meter.create_counter(f"sada.{key}.{noun}", description=description)
            self._durations[key] = self._meter.create_histogram(
                f"sada.{key}.duration",
                unit="s",
                description=f"{description}: duration",
                explicit_bucket_boundaries_advisory=list(DURATION_BUCKETS_S),
            )
        self._transitions = self._meter.create_counter(
            "sada.state.transitions", description="Orchestrator state transitions"
        )

    @contextmanager
    def track(self, operation: str, **attributes: str) -> Iterator[Tracked]:
        """Count, time and trace one operation; `status` is `ok` or `error` (the block raised)."""
        run, started = Tracked(), time.perf_counter()
        # Exceptions are not recorded on the span: their message may quote what was being processed.
        with self._tracer.start_as_current_span(
            f"sada.{operation}", attributes=attributes, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                yield run
            except BaseException as exc:
                run.fail()
                span.set_attribute("error.type", type(exc).__name__)
                raise
            finally:
                labels = {**attributes, "status": run.status}
                self._calls[operation].add(1, labels)
                self._durations[operation].record(time.perf_counter() - started, labels)
                span.set_attribute("status", run.status)
                if run.status != OK:
                    from opentelemetry.trace import StatusCode

                    span.set_status(StatusCode.ERROR)

    def state_changed(self, old: str, new: str) -> None:
        self._transitions.add(1, {"from_state": old, "to_state": new})

    def watch_orchestrator(self, snapshot: Callable[[], OrchestratorSnapshot]) -> None:
        """Gauges of the orchestrator, read from `snapshot()` at each collection."""
        from opentelemetry.metrics import Observation

        from .state import State

        def gauge(name: str, description: str, read: Callable[[OrchestratorSnapshot], float], unit: str = "1"):
            def callback(_options) -> Iterator[Observation]:
                yield Observation(read(snapshot()))

            self._meter.create_observable_gauge(name, callbacks=[callback], unit=unit, description=description)

        def current_state(_options) -> Iterator[Observation]:
            current = snapshot().state
            for state in State:  # state-set pattern: 1 for the current state, 0 for the others
                yield Observation(int(state.value == current), {"state": state.value})

        self._meter.create_observable_gauge(
            "sada.orchestrator.state", callbacks=[current_state], description="1 for the current state, 0 otherwise"
        )
        gauge("sada.stream.on", "1 while the stream is on", lambda s: int(s.stream_on))
        gauge("sada.stream.live.duration", "Seconds since the stream started", lambda s: s.stream_live_s, "s")
        gauge("sada.encoder.up", "1 while the encoder process is alive", lambda s: int(s.encoder_alive))
        gauge("sada.encoder.restarts", "Encoder restarts since the orchestrator started", lambda s: s.encoder_restarts)
        gauge("sada.ingest.connected", "1 while the video input is readable", lambda s: int(s.ingest_connected))

    def instrument_app(self, app) -> None:
        """HTTP server spans and metrics of a FastAPI app (route template, method, status only)."""
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
            excluded_urls=",".join(EXCLUDED_URLS),
            exclude_spans=["send", "receive"],  # internal ASGI spans: noise
        )

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


# --------------------------------------------------------------------------- privacy filter


class AllowlistSpanExporter:
    """Hands spans to `inner` rebuilt with allowed attributes only.

    Events (where exceptions with their message are recorded), links and the status description are
    dropped; the span name is kept (it is built from the method and the route template).
    """

    def __init__(self, inner, allowed: frozenset[str] = ALLOWED_ATTRIBUTES) -> None:
        self.inner, self.allowed = inner, allowed

    def export(self, spans: Sequence):
        return self.inner.export([self._clean(span) for span in spans])

    def _clean(self, span):
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.trace import Status

        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes={k: v for k, v in (span.attributes or {}).items() if k in self.allowed},
            events=(),
            links=(),
            kind=span.kind,
            status=Status(span.status.status_code),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


# --------------------------------------------------------------------------- configuration


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Is the export switched on? `SADA_TELEMETRY` decides when set, else an OTLP endpoint does."""
    env = os.environ if environ is None else environ
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() in TRUE:
        return False
    flag = env.get(FLAG_ENV, "").strip().lower()
    if flag in FALSE:
        return False
    return flag in TRUE or any(env.get(name, "").strip() for name in ENDPOINT_ENVS)


def build(service_name: str, mosque: str = "", *, span_exporter=None, metric_reader=None) -> Telemetry:
    """Providers wired to OTLP/HTTP (or to the given exporter / reader, for tests)."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    attributes = {"service.name": service_name}
    if mosque:
        attributes[MOSQUE_ATTRIBUTE] = mosque
    resource = Resource.create(attributes)

    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    # One view for every instrument (ours and the HTTP ones): only allowed attribute keys are aggregated.
    views = [View(instrument_name="*", attribute_keys=set(METRIC_ATTRIBUTES))]
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader], views=views)

    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor = BatchSpanProcessor(AllowlistSpanExporter(OTLPSpanExporter()))
    else:
        processor = SimpleSpanProcessor(AllowlistSpanExporter(span_exporter))
    tracer_provider.add_span_processor(processor)
    return Telemetry(meter_provider, tracer_provider)


_active: DisabledTelemetry = DisabledTelemetry()


def get() -> DisabledTelemetry:
    """The telemetry of this process (disabled until `start()` finds it switched on)."""
    return _active


def start(service_name: str, mosque: str = "", *, span_exporter=None, metric_reader=None) -> DisabledTelemetry:
    """Configure this process once at boot. Stays disabled unless the environment (or a test) asks."""
    global _active
    injected = span_exporter is not None or metric_reader is not None
    _active = (
        build(service_name, mosque, span_exporter=span_exporter, metric_reader=metric_reader)
        if (injected or enabled())
        else DisabledTelemetry()
    )
    return _active


def stop() -> None:
    """Flush and drop the telemetry of this process (tests, clean shutdown)."""
    global _active
    _active.shutdown()
    _active = DisabledTelemetry()


def tracked(operation: str, **attributes: str) -> Callable:
    """Decorator form of `track` for a function with fixed labels."""

    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            with get().track(operation, **attributes):
                return function(*args, **kwargs)

        return wrapper

    return decorate
