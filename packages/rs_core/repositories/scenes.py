"""Per-scene reflectance metadata: the quantification value + BOA offset that index math reads
per scene instead of hard-coding (CLAUDE.md invariant 2). Global to the system and immutable
once stored: the upsert is keyed by scene_id and first-write-wins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import SceneMetadata


async def upsert_scene_metadata(
    session: AsyncSession,
    *,
    scene_id: str,
    provider: str,
    quantification_value: float,
    boa_add_offset: dict[str, float],
    crs: str,
    sensing_datetime: datetime,
    processing_baseline: str | None = None,
    scene_cloud_pct: float | None = None,
) -> tuple[SceneMetadata, bool]:
    """Insert per-scene reflectance metadata if absent; return (row, created). Idempotent and
    concurrency-safe via INSERT ... ON CONFLICT DO NOTHING on the scene_id primary key."""
    stmt = (
        pg_insert(SceneMetadata)
        .values(
            scene_id=scene_id,
            provider=provider,
            quantification_value=quantification_value,
            boa_add_offset=boa_add_offset,
            crs=crs,
            sensing_datetime=sensing_datetime,
            processing_baseline=processing_baseline,
            scene_cloud_pct=scene_cloud_pct,
        )
        .on_conflict_do_nothing(index_elements=["scene_id"])
        .returning(SceneMetadata.scene_id)
    )
    created = (await session.execute(stmt)).scalar_one_or_none() is not None
    row = (
        await session.execute(select(SceneMetadata).where(SceneMetadata.scene_id == scene_id))
    ).scalar_one()
    return row, created
