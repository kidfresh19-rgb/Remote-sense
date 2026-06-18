"""Region-cluster Celery tasks (comparison groups, PRD 0002 / ADR 0010). Thin wrapper in the house
style: resolve settings to a per-task NullPool session and delegate to the version-stamped
repository recompute. Triggered on farm register / geometry-version change (ingestion) and after a
boundary seed / upload / draw / edit."""

from __future__ import annotations

import asyncio

from rs_core.config import get_settings
from rs_core.repositories.regions import recompute_farm_region_assignments
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery


@celery.task(name="regions.recompute_farm_region_assignments")
def recompute_farm_region_assignments_task(canonical_farm_id: str | None = None) -> int:
    """Re-evaluate region assignments for one farm (on register or geometry-version change) or every
    farm (`canonical_farm_id=None`, after a seed or a boundary edit). Idempotent; returns the number
    of (farm, layer) assignments written. Uses a per-task NullPool async engine so a forked worker
    never shares a connection pool across event loops (same pattern as collect_pass_task)."""
    settings = get_settings()

    async def _run() -> int:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                written = await recompute_farm_region_assignments(
                    session,
                    canonical_farm_id=canonical_farm_id,
                    edge_tolerance_m=settings.region_boundary_adjacent_tolerance_m,
                )
                await session.commit()
                return written
        finally:
            await engine.dispose()

    return asyncio.run(_run())
