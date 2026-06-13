"""The gateway publish task (L7): build + push a farm's additive results through rs_sync and
record the outcome (published / dead_letter) in the outbox."""

from __future__ import annotations

import asyncio

from rs_core import get_settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery
from services.worker.publish import gateway_from_settings, publish_farm


async def _publish_farm(canonical_farm_id: str) -> dict[str, object]:
    settings = get_settings()
    gateway = gateway_from_settings(settings)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            summary = await publish_farm(session, gateway, canonical_farm_id=canonical_farm_id)
            await session.commit()
            return {"results": summary.results, "status": summary.status}
    finally:
        await engine.dispose()


@celery.task(name="sync.publish_farm")
def publish_farm_task(canonical_farm_id: str) -> dict[str, object]:
    """Build + push a farm's additive results to the gateway (L7); record published/dead-letter."""
    return asyncio.run(_publish_farm(canonical_farm_id))
