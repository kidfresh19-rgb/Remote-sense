"""Celery application. Phase 0: wiring + a no-op heartbeat so the worker and beat boot
cleanly. Backfill, the forward-fill scheduler, and per-field state/locking land in Phase 3."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init
from kombu import Queue
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
    # S4.2: collection tasks run seconds to minutes, so a process must never hoard a prefetch
    # window of them while siblings idle; one reserved task per process pairs with acks_late
    # for fair dispatch and clean redelivery. Process-count autoscaling is the worker command's
    # --autoscale flag (docker-compose), sized per host via RS_WORKER_AUTOSCALE.
    worker_prefetch_multiplier=1,
)

# Interactive/batch queue isolation. A user-facing analysis (AOI Studio previews, the ad-hoc AOI
# button, a "collect these dates" request) must never queue behind the bulk backfill. The two share
# nothing now: interactive work routes to a dedicated `interactive` queue served by its own worker
# (docker-compose `worker-interactive`), while the forward-fill / backfill sweep keeps the default
# `celery` queue. Broker priority cannot substitute for this: kombu's Redis transport BRPOPs the
# base queue first, so a task already deep in `celery` outranks any later message regardless of
# priority - a separate queue is the only backlog-independent lane. The default-queue name is kept
# as `celery` so the existing in-flight backlog and every unrouted task are undisturbed.
celery.conf.task_default_queue = "celery"
celery.conf.task_queues = (
    Queue("celery"),
    Queue("interactive"),
)
celery.conf.task_routes = {
    # The all-passes orthophoto bundle renders dozens of COGs; it is bulk work, not a latency-
    # sensitive preview, so it stays on the default `celery` lane and off `interactive`. An exact
    # task name takes precedence over the `analysis.*` glob below (Celery resolves named routes
    # before patterns), so this override holds; the route test guards it against regressions.
    "analysis.bundle_aoi_orthophotos": {"queue": "celery"},
    # Every ad-hoc AOI analysis (analysis.analyse_aoi*, analysis.analyse_farm_series*) is a
    # latency-sensitive preview, never persisted - all of them belong on the reserved lane.
    "analysis.*": {"queue": "interactive"},
    # The targeted "collect specific dates" planner is user-initiated; it (and the per-scene
    # collect_pass it fans out, enqueued with queue="interactive" in tasks/collection.py) runs on
    # the interactive lane so a user's request is not stuck behind the daily sweep.
    "collection.collect_dates_field": {"queue": "interactive"},
}

# Forward-fill runs on Sentinel-2 cadence (~5 days); the scheduler checks daily and enqueues only
# the fields actually due, plus any flagged for backfill (services.worker.tasks.scan_and_enqueue).
# Weekly off-peak maintenance: partition upkeep (S4.1) first - months must exist before anything
# writes into them - then the COG retention prune (S4.3), both clear of the daily scan window.
celery.conf.beat_schedule = {
    "collection-scan-and-enqueue": {
        "task": "collection.scan_and_enqueue",
        "schedule": crontab(hour=2, minute=0),
    },
    "maintenance-ensure-analysis-partitions": {
        "task": "maintenance.ensure_analysis_partitions",
        "schedule": crontab(day_of_week="sun", hour=2, minute=30),
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
