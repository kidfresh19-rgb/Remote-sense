"""Celery task surface for the worker, one submodule per wire namespace: collection.*,
interpret.*, sync.*, analysis.*, maintenance.*. Every task carries an explicit name=, so the
registered wire name never depends on the module path, and celery_app's autodiscover imports
this package to register them all.

The tasks are deliberately thin: they resolve config -> session/redis/adapter and delegate to
injectable async orchestrators (testable against the mock adapter + a real session + a fake
lock, with no broker). Each task runs its own event loop (asyncio.run) over a per-task NullPool
engine, so a forked Celery worker never shares an async connection pool across loops. Every
public name is re-exported here so callers keep importing from services.worker.tasks.
"""

from services.worker.tasks.analysis import (
    analyse_aoi_series_multi_task,
    analyse_aoi_series_task,
    analyse_aoi_task,
    analyse_farm_series_multi_task,
    analyse_farm_series_task,
    render_natural_color_task,
)
from services.worker.tasks.backfill_rgb import backfill_rgb_cog
from services.worker.tasks.bundle import bundle_aoi_orthophotos_task
from services.worker.tasks.collection import (
    CORE_INDICES,
    CollectionSummary,
    backfill_field,
    collect_dates_field,
    collect_pass,
    collect_pass_task,
    due_field_ids,
    field_to_aoi,
    forward_fill_field,
    plan_backfill_scenes,
    prepare_and_run,
    run_collection,
    scan_and_enqueue,
)
from services.worker.tasks.interpret import interpret_field_pass_task
from services.worker.tasks.maintenance import ensure_analysis_partitions_task, prune_cogs_task
from services.worker.tasks.regions import recompute_farm_region_assignments_task
from services.worker.tasks.sync import publish_farm_task

__all__ = [
    "CORE_INDICES",
    "CollectionSummary",
    "analyse_aoi_series_multi_task",
    "backfill_rgb_cog",
    "bundle_aoi_orthophotos_task",
    "analyse_aoi_series_task",
    "analyse_aoi_task",
    "analyse_farm_series_multi_task",
    "analyse_farm_series_task",
    "backfill_field",
    "collect_dates_field",
    "collect_pass",
    "collect_pass_task",
    "due_field_ids",
    "ensure_analysis_partitions_task",
    "field_to_aoi",
    "forward_fill_field",
    "interpret_field_pass_task",
    "plan_backfill_scenes",
    "prepare_and_run",
    "prune_cogs_task",
    "publish_farm_task",
    "recompute_farm_region_assignments_task",
    "render_natural_color_task",
    "run_collection",
    "scan_and_enqueue",
]
