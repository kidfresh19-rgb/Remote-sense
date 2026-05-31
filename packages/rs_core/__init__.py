"""rs_core: shared foundations for remote-sense (config, logging, domain primitives)."""

from rs_core.config import Settings, get_settings
from rs_core.logging import configure_logging, get_logger
from rs_core.telemetry import (
    configure_telemetry,
    get_tracer,
    instrument_celery,
    instrument_fastapi,
    instrument_httpx,
)

__all__ = [
    "Settings",
    "get_settings",
    "configure_logging",
    "get_logger",
    "configure_telemetry",
    "get_tracer",
    "instrument_celery",
    "instrument_fastapi",
    "instrument_httpx",
]
