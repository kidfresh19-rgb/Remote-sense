"""Celery application. Phase 0: wiring + a no-op heartbeat so the worker and beat boot
cleanly. Backfill, the forward-fill scheduler, and per-field state/locking land in Phase 3."""

from __future__ import annotations

from celery import Celery
from rs_core import get_settings

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


@celery.task(name="heartbeat")
def heartbeat() -> str:
    """Placeholder task proving the broker round-trips. Replaced by collection tasks."""
    return "ok"
