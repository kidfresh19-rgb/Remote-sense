"""OpenTelemetry wiring for remote-sense.

Tracing is opt-in (``RS_OTEL_ENABLED``) and degrades to a safe no-op when the
opentelemetry packages or an OTLP endpoint are absent, so the API and worker boot
identically with or without an observability backend. The instrumentors for FastAPI,
Celery and httpx are wired here so request, task and outbound-call spans connect into one
trace across the spine (Gateway ▸ ingestion ▸ pipeline ▸ access layer ▸ sync).

Logs are correlated to traces by ``rs_core.logging._add_trace_context``, which stamps the
active trace/span ids onto every structured log event."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from rs_core.config import Settings, get_settings
from rs_core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger("rs_core.telemetry")

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised only when OTel is absent
    _OTEL_AVAILABLE = False

_configured = False


def configure_telemetry(settings: Settings | None = None) -> bool:
    """Install the global tracer provider + OTLP exporter.

    Returns True when tracing is live, False when it stayed a no-op (disabled, packages
    missing, or no endpoint configured). Idempotent: safe to call from both the API
    lifespan and the Celery worker bootstrap."""
    global _configured
    settings = settings or get_settings()

    if _configured:
        return True
    if not settings.otel_enabled:
        log.info("otel.disabled")
        return False
    if not _OTEL_AVAILABLE:
        log.warning("otel.unavailable", detail="opentelemetry packages not installed")
        return False

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "deployment.environment": settings.app_env,
        }
    )
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        log.info("otel.configured", endpoint=endpoint)
    else:
        log.warning("otel.no_endpoint", detail="spans created but not exported")

    trace.set_tracer_provider(provider)
    _configured = True
    return True


def instrument_fastapi(app: FastAPI) -> None:
    """Instrument a FastAPI app for request spans. No-op when OTel is unavailable."""
    if not _OTEL_AVAILABLE:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ModuleNotFoundError:  # pragma: no cover
        log.warning("otel.fastapi_instrumentor_missing")
        return
    FastAPIInstrumentor.instrument_app(app)


def instrument_celery() -> None:
    """Instrument Celery so task execution joins the trace. No-op when OTel unavailable."""
    if not _OTEL_AVAILABLE:
        return
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
    except ModuleNotFoundError:  # pragma: no cover
        log.warning("otel.celery_instrumentor_missing")
        return
    CeleryInstrumentor().instrument()


def instrument_httpx() -> None:
    """Instrument httpx so outbound calls (CDSE access, gateway push) emit client spans.
    No-op when OTel is unavailable."""
    if not _OTEL_AVAILABLE:
        return
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ModuleNotFoundError:  # pragma: no cover
        log.warning("otel.httpx_instrumentor_missing")
        return
    HTTPXClientInstrumentor().instrument()


class _NoOpSpan:
    """Stand-in span so ``with get_tracer(...).start_as_current_span(...)`` is always safe,
    even with OTel uninstalled. Mirrors the slice of the Span API the codebase uses."""

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None: ...

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None: ...

    def set_status(self, *_args: Any, **_kwargs: Any) -> None: ...


class _NoOpTracer:
    def start_as_current_span(self, *_args: Any, **_kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()


def get_tracer(name: str) -> Any:
    """Return a tracer for ``name``. Falls back to a no-op tracer when OTel is absent, so
    callers can open spans unconditionally without guarding every call site."""
    if not _OTEL_AVAILABLE:
        return _NoOpTracer()
    return trace.get_tracer(name)
