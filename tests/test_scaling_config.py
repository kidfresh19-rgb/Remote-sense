"""Scaling configuration (S4.2 + S4.4): pool tuning flows from settings into the engine kwargs,
the read-engine switch falls back to the primary unless a distinct replica DSN is configured,
and the worker reserves one task per process. Pure - no DB, no broker; every Settings passes
_env_file=None so a developer's real .env can never flip an outcome."""

from __future__ import annotations

from rs_core.config import Settings, aoi_pass_concurrency
from rs_core.db import engine_kwargs, read_database_url


def test_engine_kwargs_reflect_pool_settings() -> None:
    settings = Settings(
        _env_file=None,
        db_pool_size=7,
        db_max_overflow=3,
        db_pool_timeout_s=12.5,
        db_pool_recycle_s=600,
    )
    kwargs = engine_kwargs(settings)
    assert kwargs["pool_size"] == 7
    assert kwargs["max_overflow"] == 3
    assert kwargs["pool_timeout"] == 12.5
    assert kwargs["pool_recycle"] == 600
    # Dead-connection defense is not configurable away.
    assert kwargs["pool_pre_ping"] is True


def test_engine_kwargs_default_to_sqlalchemy_defaults() -> None:
    kwargs = engine_kwargs(Settings(_env_file=None))
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 10


def test_reads_stay_on_primary_until_a_replica_is_configured() -> None:
    assert read_database_url(Settings(_env_file=None)) is None


def test_read_url_equal_to_primary_means_primary() -> None:
    url = "postgresql+psycopg://rs:rs@db:5432/remote_sense"
    settings = Settings(_env_file=None, database_url=url, database_read_url=url)
    assert read_database_url(settings) is None


def test_distinct_read_url_routes_reads_to_the_replica() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://rs:rs@primary:5432/remote_sense",
        database_read_url="postgresql+psycopg://rs:rs@replica:5432/remote_sense",
    )
    assert read_database_url(settings) == "postgresql+psycopg://rs:rs@replica:5432/remote_sense"


def test_worker_reserves_one_task_per_process() -> None:
    # acks_late + prefetch 1: fair dispatch for long collection tasks, clean redelivery on a
    # worker death (S4.2).
    from services.worker.celery_app import celery

    assert celery.conf.worker_prefetch_multiplier == 1
    assert celery.conf.task_acks_late is True


# -- Interactive/batch queue isolation: user-facing analyses must not queue behind the bulk
# backfill on a shared FIFO queue. They route to a dedicated `interactive` queue served by its own
# worker, so a deep `celery` backlog can never starve a latency-sensitive request. -------------


def _route_queue(task_name: str) -> str:
    from services.worker.celery_app import celery

    queue = celery.amqp.router.route({}, task_name)["queue"]
    return queue.name if hasattr(queue, "name") else str(queue)


def test_interactive_analyses_route_to_the_interactive_queue() -> None:
    # AOI Studio previews, the ad-hoc AOI button, and user-requested date collection are all
    # latency-sensitive and must land on the reserved lane.
    for task in (
        "analysis.analyse_aoi",
        "analysis.analyse_aoi_series",
        "analysis.analyse_aoi_series_multi",
        "analysis.analyse_farm_series",
        "analysis.analyse_farm_series_multi",
        "collection.collect_dates_field",
    ):
        assert _route_queue(task) == "interactive", task


def test_bulk_backfill_stays_on_the_default_queue() -> None:
    # The forward-fill / backfill sweep is throughput work; it keeps the default `celery` queue so
    # it can never share a lane with interactive requests.
    for task in (
        "collection.collect_pass",
        "collection.backfill_field",
        "collection.forward_fill_field",
        "collection.scan_and_enqueue",
    ):
        assert _route_queue(task) == "celery", task


# -- AOI Studio pass-level concurrency (ADR 0011): min(2 * rps, 16), default when off ----------


def test_aoi_pass_concurrency_tracks_twice_the_rate() -> None:
    assert aoi_pass_concurrency(Settings(_env_file=None, cdse_rate_limit_rps=4)) == 8


def test_aoi_pass_concurrency_is_capped_at_16() -> None:
    assert aoi_pass_concurrency(Settings(_env_file=None, cdse_rate_limit_rps=20)) == 16


def test_aoi_pass_concurrency_defaults_without_a_configured_rate() -> None:
    assert aoi_pass_concurrency(Settings(_env_file=None, cdse_rate_limit_rps=None)) == 8
