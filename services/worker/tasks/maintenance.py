"""Storage maintenance tasks (S4.3): the weekly COG retention prune. Thin like every task here:
resolve config -> store + session, delegate to the injectable orchestrator
(services.worker.retention.prune_cogs), one event loop per run over a NullPool engine."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from rs_core import get_settings
from rs_core.storage import cog_store_from_settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery
from services.worker.retention import prune_cogs


async def _prune_cogs() -> dict[str, int]:
    settings = get_settings()
    store = cog_store_from_settings(settings)
    if store is None:
        # No boto3 / object store on this host: nothing was ever emitted, nothing to prune.
        return {"examined": 0, "pruned_stale_version": 0, "pruned_aged_out": 0}
    months = (
        settings.cog_retention_months
        if settings.cog_retention_months is not None
        else settings.backfill_months
    )
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            summary = await prune_cogs(
                session,
                store,
                today=datetime.now(UTC).date(),
                retention_months=months,
            )
            await session.commit()
            return {
                "examined": summary.examined,
                "pruned_stale_version": summary.pruned_stale_version,
                "pruned_aged_out": summary.pruned_aged_out,
            }
    finally:
        await engine.dispose()


@celery.task(name="maintenance.prune_cogs")
def prune_cogs_task() -> dict[str, int]:
    """Prune index-preview COGs at stale geometry versions or beyond the retention horizon
    (S4.3, risk S-1). Stats and provenance rows are never touched."""
    return asyncio.run(_prune_cogs())
