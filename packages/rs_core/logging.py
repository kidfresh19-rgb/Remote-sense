"""Structured JSON logging via structlog. Adapter calls are instrumented for latency,
error rate and quota burn at the rs_imagery boundary (not configured here)."""

from __future__ import annotations

import logging
from typing import Any

import structlog

try:  # OTel is optional; logs stay valid JSON whether or not tracing is installed.
    from opentelemetry import trace as _otel_trace
except ModuleNotFoundError:  # pragma: no cover - exercised only when OTel is absent
    _otel_trace = None


def _add_trace_context(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: stamp the active trace/span ids onto each event so logs and
    traces cross-reference. No-op when OTel is absent or no span is active."""
    if _otel_trace is None:
        return event_dict
    ctx = _otel_trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
