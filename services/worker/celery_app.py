"""Celery application. Phase 0: wiring + a no-op heartbeat so the worker and beat boot
cleanly. Backfill, the forward-fill scheduler, and per-field state/locking land in Phase 3."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
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

# Forward-fill runs on Sentinel-2 cadence (~5 days); the scheduler checks daily and enqueues only
# the fields actually due, plus any flagged for backfill (services.worker.tasks.scan_and_enqueue).
# COG retention (S4.3) prunes weekly, off-peak and after the daily scan window.
celery.conf.beat_schedule = {
    "collection-scan-and-enqueue": {
        "task": "collection.scan_and_enqueue",
        "schedule": crontab(hour=2, minute=0),
    },
    "maintenance-prune-cogs": {
        "task": "maintenance.prune_cogs",
        "schedule": crontab(day_of_week="sun", hour=3, minute=0),
    },
}

# Register the collection tasks (services.worker.tasks) on the worker/beat.
celery.autodiscover_tasks(["services.worker"])


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
