"""Phase 0 tests for telemetry wiring. The contract is graceful degradation: tracing must
be a safe no-op when disabled or when the OTel packages are absent, and the log trace-id
processor and tracer fallback must never raise regardless of OTel availability."""

from __future__ import annotations

from rs_core import configure_telemetry, get_tracer
from rs_core.config import Settings
from rs_core.logging import _add_trace_context


def test_disabled_is_noop():
    assert configure_telemetry(Settings(otel_enabled=False)) is False


def test_enabled_without_packages_degrades(monkeypatch):
    # In this environment the OTel SDK is not installed, so even enabled tracing stays a
    # no-op rather than crashing the API/worker boot.
    import rs_core.telemetry as tel

    monkeypatch.setattr(tel, "_OTEL_AVAILABLE", False)
    monkeypatch.setattr(tel, "_configured", False)
    assert tel.configure_telemetry(Settings(otel_enabled=True)) is False


def test_trace_processor_is_safe_without_active_span():
    event = {"event": "hello", "level": "info"}
    out = _add_trace_context(None, "info", dict(event))
    # No active span (and/or no OTel): the event passes through untouched, never raising.
    assert out["event"] == "hello"


def test_get_tracer_returns_usable_span():
    tracer = get_tracer("test")
    with tracer.start_as_current_span("unit") as span:
        span.set_attribute("k", "v")  # must not raise even on the no-op tracer
