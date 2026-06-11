"""The interpretation draft task (L4b): draft + store an unpublished agronomic read for one
field/pass. Never publishes - publishing is the agronomist review action's job (risk #6)."""

from __future__ import annotations

import asyncio
import uuid

from rs_core import Field, get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery
from services.worker.interpret import interpret_field_pass


async def _interpret_for_pass(field_id: str, scene_id: str) -> dict[str, object]:
    from rs_interpret.client import AnthropicInterpretClient  # lazy: needs the `interpret` extra

    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            field = (
                await session.execute(select(Field).where(Field.id == uuid.UUID(field_id)))
            ).scalar_one()
            result = await interpret_field_pass(
                session,
                AnthropicInterpretClient(settings),
                field_id=field.id,
                scene_id=scene_id,
                geometry_version=field.geometry_version,
                crop=field.crop,
                model_id=settings.anthropic_model,
            )
            await session.commit()
            if result is None:
                return {"skipped": True}
            return {"status": result.status, "confidence": result.confidence, "published": False}
    finally:
        await engine.dispose()


@celery.task(name="interpret.field_pass")
def interpret_field_pass_task(field_id: str, scene_id: str) -> dict[str, object]:
    """Draft + store an unpublished agronomic read for one field/pass (L4b). Never publishes."""
    return asyncio.run(_interpret_for_pass(field_id, scene_id))
