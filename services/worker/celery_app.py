"""Celery application. Phase 0: wiring + a no-op heartbeat so the worker and beat boot
cleanly. Backfill, the forward-fill scheduler, and per-field state/locking land in Phase 3."""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init
from rs_core import (
    configure_logging,
    configure_telemetry,
    get_settings,
    instrument_celery,
    instrument_httpx,
)

settings = get_settings()

celery = Celery(
    "remote_sense",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery.conf.update(
    task_track_started=True,
    task_acks_late=True,
    timezone="UTC",
    enable_utc=True,
)


@worker_process_init.connect
def _bootstrap_observability(**_kwargs: object) -> None:
    """Configure logging + tracing per worker process. Celery forks workers, so the OTel
    SDK must be initialised after the fork, not at import time."""
    configure_logging(settings.log_level)
    configure_telemetry(settings)
    instrument_celery()
    instrument_httpx()


@celery.task(name="heartbeat")
def heartbeat() -> str:
    """Placeholder task proving the broker round-trips. Replaced by collection tasks."""
    return "ok"
