"""RGB COG backfill task (backlog 0024): for each registered-field pass that has index COGs but
no rgb.tif, fetch B02/B03/B04 from CDSE and write the RGB reflectance COG at the geometry_version
the analysis row was stored under. Runs on the default bulk Celery queue, which is already
isolated from the `interactive` lane, so it cannot starve user-facing AOI Studio previews.

Idempotent: a pass with an existing rgb.tif is a no-op. The `geometry_version` key parameter
MUST come from the analysis row, not the field's current geometry_version, or the COG is written
at a path the tiler never reads (orphaned silently)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from geoalchemy2.shape import to_shape
from rs_core import Field, cog_key, cog_store_from_settings, get_settings
from rs_core.logging import get_logger
from rs_imagery import AOI, TimeRange, get_access_adapter
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery
from services.worker.tasks.analysis import _render_rgb_cog

log = get_logger(__name__)


def _start_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@celery.task(name="collection.backfill_rgb_cog", bind=True, max_retries=3, default_retry_delay=120)
def backfill_rgb_cog(
    self: Any,
    field_id: str,
    scene_id: str,
    geometry_version: int,
    pass_date: str,
) -> dict[str, object]:
    """Fetch B02/B03/B04 for one registered-field pass and write rgb.tif at the
    geometry_version the existing index COGs were stored under. Idempotent: returns
    immediately when rgb.tif already exists. `pass_date` (ISO date string, from the
    analysis row sensing_datetime) bounds the CDSE search to avoid a full catalogue
    scan. CDSE quota errors are handled by the adapter's internal tenacity retry;
    other transient errors retry up to `max_retries` with exponential backoff."""
    settings = get_settings()
    store = cog_store_from_settings(settings)
    if store is None:
        log.warning("backfill_rgb_cog.no_store", field_id=field_id, scene_id=scene_id)
        return {"skipped": True, "reason": "no_store"}

    key = cog_key(
        field_id=uuid.UUID(field_id),
        scene_id=scene_id,
        index="rgb",
        geometry_version=geometry_version,
    )
    if store.exists(key):
        log.info(
            "backfill_rgb_cog.already_exists",
            field_id=field_id,
            scene_id=scene_id,
            key=key,
        )
        return {"skipped": True, "reason": "exists"}

    async def _run() -> bytes:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                field = (
                    await session.execute(select(Field).where(Field.id == uuid.UUID(field_id)))
                ).scalar_one()
                geometry: dict[str, Any] = mapping(to_shape(field.boundary))
        finally:
            await engine.dispose()

        adapter = get_access_adapter(settings)
        aoi = AOI(geometry=geometry, crs="EPSG:4326")
        target = date.fromisoformat(pass_date)
        search_range = TimeRange(
            start=_start_of_day(target) - timedelta(days=1),
            end=_start_of_day(target) + timedelta(days=2),
        )
        scenes = await adapter.search(aoi, search_range, max_scene_cloud_pct=100.0)
        scene = next((s for s in scenes if s.scene_id == scene_id), None)
        if scene is None:
            raise LookupError(
                f"scene {scene_id!r} not found near {pass_date!r} for field {field_id}"
            )

        fetched = await adapter.fetch(
            scene, aoi, bands=sorted(["B02", "B03", "B04"]), resolution_m=10.0
        )
        return _render_rgb_cog(fetched.data.bands, fetched.data.transform, fetched.data.crs)

    try:
        cog_bytes = asyncio.run(_run())
    except LookupError as exc:
        log.error(
            "backfill_rgb_cog.scene_not_found",
            field_id=field_id,
            scene_id=scene_id,
            pass_date=pass_date,
            error=str(exc),
        )
        return {"skipped": True, "reason": "scene_not_found"}
    except Exception as exc:
        log.warning(
            "backfill_rgb_cog.retry",
            field_id=field_id,
            scene_id=scene_id,
            error=str(exc),
            retries=self.request.retries,
        )
        raise self.retry(exc=exc, countdown=120 * (2**self.request.retries)) from exc

    store.put(key, cog_bytes, content_type="image/tiff")
    log.info(
        "backfill_rgb_cog.written",
        field_id=field_id,
        scene_id=scene_id,
        geometry_version=geometry_version,
        key=key,
        bytes=len(cog_bytes),
    )
    return {"written": True, "key": key, "bytes": len(cog_bytes)}
