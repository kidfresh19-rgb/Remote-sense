"""Live collection tasks (Phase 3, D4-live): the backfill and forward-fill that wire the planning
kernel, the lock-gated collection, and DB persistence into one resumable unit of work, plus the
beat scan that enqueues them.

The Celery tasks are deliberately thin: they resolve config -> session/redis/adapter and delegate
to `run_collection` / `prepare_and_run`, the injectable async orchestrators that hold the logic
(so they are testable against the mock adapter + a real session + a fake lock, with no broker).
Each task runs its own event loop (`asyncio.run`) over a per-task NullPool engine, so a forked
Celery worker never shares an async connection pool across loops."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import redis.asyncio as aioredis
from geoalchemy2.shape import to_shape
from rs_analysis import analyze_index, get_index
from rs_core import (
    CogStore,
    Field,
    FieldCollectionState,
    advance_cursor,
    cog_key,
    cog_store_from_settings,
    get_collection_state,
    get_settings,
    mark_backfill_complete,
    processed_scene_ids,
    record_forward_fill_poll,
    upsert_scene_metadata,
)
from rs_imagery import AOI, AccessPort, TimeRange, get_access_adapter
from shapely.geometry import mapping
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery
from services.worker.collection import collect_field_locked
from services.worker.interpret import interpret_field_pass
from services.worker.locks import DEFAULT_LOCK_TTL_SECONDS, LockClient
from services.worker.persistence import persist_analysis_output
from services.worker.planning import (
    SENTINEL2_REVISIT_DAYS,
    backfill_window,
    collection_key,
    field_collection_key,
    plan_scenes,
    select_forward_fill_due,
)
from services.worker.publish import gateway_from_settings, publish_farm

# The core indices stored on every usable pass (PLAN §5). Adding one is a config change here, not
# a schema migration - the analysis row is index-agnostic.
CORE_INDICES = ["ndvi", "evi2", "savi", "ndre", "ndmi"]


@dataclass(frozen=True)
class CollectionSummary:
    """Outcome of one field collection run. `locked` means another worker held the unit (R-1) and
    this run did nothing."""

    locked: bool
    scenes: int
    analyses: int
    cursor_date: date | None = None


def _summary_dict(summary: CollectionSummary) -> dict[str, object]:
    """JSON-safe form for a Celery result (date -> isoformat)."""
    return {
        "locked": summary.locked,
        "scenes": summary.scenes,
        "analyses": summary.analyses,
        "cursor_date": summary.cursor_date.isoformat() if summary.cursor_date else None,
    }


def field_to_aoi(field: Field) -> AOI:
    """The field's stored WGS84 boundary as an analysis AOI. The polygon is the analysis unit (a
    no-field farm's field is its own boundary, DI-4)."""
    return AOI(geometry=mapping(to_shape(field.boundary)), crs="EPSG:4326")


def _start_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def run_collection(
    session: AsyncSession,
    lock_client: LockClient,
    adapter: AccessPort,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    aoi: AOI,
    time_range: TimeRange,
    indices: list[str],
    collection_key: str,
    already_processed: frozenset[str] = frozenset(),
    is_backfill: bool = False,
    cog_store: CogStore | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> CollectionSummary:
    """The body both collection tasks share: collect a field over a time range under its enqueue
    lock, persist every result (scene metadata + per-index zonal stats), and advance the cursor.
    When `cog_store` is given, also emit + store each index COG for the tiler (D1/D7). Returns a
    summary; `locked=True` means another worker held the unit and we did nothing (R-1). Runs inside
    the caller's transaction - the caller commits."""
    results = await collect_field_locked(
        lock_client=lock_client,
        collection_key=collection_key,
        adapter=adapter,
        aoi=aoi,
        time_range=time_range,
        indices=indices,
        already_processed=already_processed,
        emit_rasters=cog_store is not None,
        ttl_seconds=ttl_seconds,
    )
    if results is None:
        return CollectionSummary(locked=True, scenes=0, analyses=0)

    latest_pass: date | None = None
    latest_scene: str | None = None
    analyses = 0
    for result in results:
        # Scene metadata first: the analysis rows FK onto it (first-write-wins, immutable).
        meta = await adapter.metadata(result.scene_id)
        await upsert_scene_metadata(
            session,
            scene_id=result.scene_id,
            provider=result.provider,
            quantification_value=meta.quantification_value,
            boa_add_offset=meta.boa_add_offset,
            crs=meta.crs,
            sensing_datetime=result.sensing_datetime,
            processing_baseline=meta.processing_baseline,
            scene_cloud_pct=meta.scene_cloud_pct,
        )
        pass_date = result.sensing_datetime.date()
        for output in result.outputs:
            # When a COG is emitted for this index, stamp its object key on the row so a stored
            # analysis points at the raster it produced (provenance, invariant 5).
            cog_uri = (
                cog_key(
                    field_id=field_id,
                    scene_id=result.scene_id,
                    index=output.index_name,
                    geometry_version=geometry_version,
                )
                if cog_store is not None and output.index_name in result.rasters
                else None
            )
            await persist_analysis_output(
                session,
                output,
                field_id=field_id,
                scene_id=result.scene_id,
                pass_date=pass_date,
                geometry_version=geometry_version,
                provider=result.provider,
                provider_scene_id=result.scene_id,
                processing_mode=result.processing_mode,
                cog_uri=cog_uri,
            )
            analyses += 1
        if cog_store is not None and result.rasters:
            from rs_analysis import write_cog  # lazy: rasterio (geo extra), in-container only

            for name, raster in result.rasters.items():
                cog_store.put(
                    cog_key(
                        field_id=field_id,
                        scene_id=result.scene_id,
                        index=name,
                        geometry_version=geometry_version,
                    ),
                    write_cog(raster.array, transform=raster.transform, crs=raster.crs),
                )
        if advance_cursor(latest_pass, pass_date) != latest_pass:
            latest_pass, latest_scene = pass_date, result.scene_id

    when = now or datetime.now(UTC)
    if is_backfill:
        await mark_backfill_complete(
            session,
            field_id=field_id,
            geometry_version=geometry_version,
            completed_at=when,
            cursor_date=latest_pass,
        )
        await session.execute(
            update(Field).where(Field.id == field_id).values(needs_backfill=False)
        )
    else:
        await record_forward_fill_poll(
            session,
            field_id=field_id,
            geometry_version=geometry_version,
            polled_at=when,
            cursor_date=latest_pass,
            last_scene_id=latest_scene,
        )
    return CollectionSummary(
        locked=False, scenes=len(results), analyses=analyses, cursor_date=latest_pass
    )


async def prepare_and_run(
    session: AsyncSession,
    lock_client: LockClient,
    adapter: AccessPort,
    *,
    field_id: uuid.UUID,
    is_backfill: bool,
    backfill_months: int,
    indices: list[str],
    cog_store: CogStore | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> CollectionSummary:
    """Load a field, resolve its AOI + time range + already-processed set, and run a collection.
    Backfill covers the whole backfill window; forward-fill covers since the stored cursor (or the
    window start if the field has never been collected)."""
    when = now or datetime.now(UTC)
    field = (await session.execute(select(Field).where(Field.id == field_id))).scalar_one()
    geometry_version = field.geometry_version
    aoi = field_to_aoi(field)
    already = await processed_scene_ids(
        session, field_id=field_id, geometry_version=geometry_version
    )
    window_start, _ = backfill_window(when.date(), backfill_months)
    if is_backfill:
        start = window_start
    else:
        state = await get_collection_state(
            session, field_id=field_id, geometry_version=geometry_version
        )
        start = state.cursor_date if state and state.cursor_date else window_start
    time_range = TimeRange(start=_start_of_day(start), end=when)
    return await run_collection(
        session,
        lock_client,
        adapter,
        field_id=field_id,
        geometry_version=geometry_version,
        aoi=aoi,
        time_range=time_range,
        indices=indices,
        collection_key=field_collection_key(str(field_id), geometry_version),
        already_processed=already,
        is_backfill=is_backfill,
        cog_store=cog_store,
        now=when,
        ttl_seconds=ttl_seconds,
    )


async def plan_backfill_scenes(
    session: AsyncSession,
    adapter: AccessPort,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    aoi: AOI,
    months: int,
    now: datetime,
) -> list[tuple[str, str]]:
    """The not-yet-processed passes in the field's backfill window, as (scene_id, pass_date) pairs
    to fan one task out per pass (D11). Dedups against the scenes already stored for this field at
    this geometry version (R-1), so a re-scan enqueues only what is missing - which is also how the
    backfill converges: an empty plan means the window is fully collected."""
    window_start, _ = backfill_window(now.date(), months)
    scenes = await adapter.search(aoi, TimeRange(start=_start_of_day(window_start), end=now))
    already = await processed_scene_ids(
        session, field_id=field_id, geometry_version=geometry_version
    )
    by_id = {s.scene_id: s for s in scenes}
    return [
        (scene_id, by_id[scene_id].sensing_datetime.date().isoformat())
        for scene_id in plan_scenes([s.scene_id for s in scenes], already)
    ]


async def collect_pass(
    session: AsyncSession,
    lock_client: LockClient,
    adapter: AccessPort,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    aoi: AOI,
    scene_id: str,
    pass_date: date,
    indices: list[str],
    cog_store: CogStore | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> CollectionSummary:
    """Collect a single backfill pass (one scene) under its per-scene lock (R-1, D11). The one-day
    search window resolves to just that scene; persistence and the cursor advance reuse
    run_collection. `is_backfill=False` so the pass advances the cursor without prematurely marking
    the field's backfill complete - convergence is decided by plan_backfill_scenes."""
    already = await processed_scene_ids(
        session, field_id=field_id, geometry_version=geometry_version
    )
    day_start = _start_of_day(pass_date)
    return await run_collection(
        session,
        lock_client,
        adapter,
        field_id=field_id,
        geometry_version=geometry_version,
        aoi=aoi,
        time_range=TimeRange(start=day_start, end=day_start + timedelta(days=1)),
        indices=indices,
        collection_key=collection_key(str(field_id), scene_id, geometry_version),
        already_processed=already,
        is_backfill=False,
        cog_store=cog_store,
        now=now,
        ttl_seconds=ttl_seconds,
    )


async def due_field_ids(
    session: AsyncSession, *, now: datetime, cadence_days: int = SENTINEL2_REVISIT_DAYS
) -> tuple[list[str], list[str]]:
    """Fields the scheduler should enqueue: those flagged for (re)backfill, and those whose
    backfill is complete and are due a forward-fill poll by cadence. Enqueue-time dedup + the lock
    (R-1) make an overlap with an in-flight run harmless."""
    backfill = (
        (await session.execute(select(Field.id).where(Field.needs_backfill.is_(True))))
        .scalars()
        .all()
    )
    candidates = (
        await session.execute(
            select(
                Field.id,
                FieldCollectionState.last_poll_at,
                FieldCollectionState.backfill_complete,
            )
            # Join the CURRENT geometry version's state only - a boundary change leaves stale
            # states for old versions; cadence decisions must use the live one (and not double-row).
            .join(
                FieldCollectionState,
                (FieldCollectionState.field_id == Field.id)
                & (FieldCollectionState.geometry_version == Field.geometry_version),
            )
            .where(Field.needs_backfill.is_(False))
        )
    ).all()
    forward = select_forward_fill_due(
        ((str(fid), last_poll, complete) for fid, last_poll, complete in candidates),
        now,
        cadence_days=cadence_days,
    )
    return [str(fid) for fid in backfill], forward


async def _run_for_field(field_id: str, *, is_backfill: bool) -> dict[str, object]:
    settings = get_settings()
    adapter = get_access_adapter(settings)
    redis = aioredis.Redis.from_url(settings.redis_url)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            summary = await prepare_and_run(
                session,
                redis,
                adapter,
                field_id=uuid.UUID(field_id),
                is_backfill=is_backfill,
                backfill_months=settings.backfill_months,
                indices=CORE_INDICES,
                cog_store=cog_store_from_settings(settings),
            )
            await session.commit()
        return _summary_dict(summary)
    finally:
        await redis.aclose()
        await engine.dispose()


async def _fan_out_backfill(field_id: str) -> dict[str, object]:
    """Plan the field's outstanding backfill passes and enqueue one collect_pass task per pass; if
    none remain, the window is fully collected, so mark the backfill complete and clear the flag
    (D11). Each pass then runs and commits independently under its own per-scene lock."""
    settings = get_settings()
    adapter = get_access_adapter(settings)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    now = datetime.now(UTC)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            field = (
                await session.execute(select(Field).where(Field.id == uuid.UUID(field_id)))
            ).scalar_one()
            geometry_version = field.geometry_version
            plan = await plan_backfill_scenes(
                session,
                adapter,
                field_id=field.id,
                geometry_version=geometry_version,
                aoi=field_to_aoi(field),
                months=settings.backfill_months,
                now=now,
            )
            if not plan:
                await mark_backfill_complete(
                    session,
                    field_id=field.id,
                    geometry_version=geometry_version,
                    completed_at=now,
                )
                await session.execute(
                    update(Field).where(Field.id == field.id).values(needs_backfill=False)
                )
                await session.commit()
    finally:
        await engine.dispose()
    if not plan:
        return {"fanned_out": 0, "complete": True}
    for scene_id, pass_date in plan:
        collect_pass_task.delay(field_id, scene_id, pass_date)
    return {"fanned_out": len(plan), "complete": False}


async def _collect_one_pass(field_id: str, scene_id: str, pass_date: str) -> dict[str, object]:
    settings = get_settings()
    adapter = get_access_adapter(settings)
    redis = aioredis.Redis.from_url(settings.redis_url)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            field = (
                await session.execute(select(Field).where(Field.id == uuid.UUID(field_id)))
            ).scalar_one()
            summary = await collect_pass(
                session,
                redis,
                adapter,
                field_id=field.id,
                geometry_version=field.geometry_version,
                aoi=field_to_aoi(field),
                scene_id=scene_id,
                pass_date=date.fromisoformat(pass_date),
                indices=CORE_INDICES,
                cog_store=cog_store_from_settings(settings),
            )
            await session.commit()
        return _summary_dict(summary)
    finally:
        await redis.aclose()
        await engine.dispose()


async def _scan_and_enqueue() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            backfill_ids, forward_ids = await due_field_ids(session, now=datetime.now(UTC))
    finally:
        await engine.dispose()
    for field_id in backfill_ids:
        backfill_field.delay(field_id)
    for field_id in forward_ids:
        forward_fill_field.delay(field_id)
    return {"backfill": len(backfill_ids), "forward_fill": len(forward_ids)}


@celery.task(name="collection.backfill_field")
def backfill_field(field_id: str) -> dict[str, object]:
    """Fan a field's full-history backfill into one collect_pass task per outstanding pass (D11);
    a later scan that finds no passes left converges it to backfill-complete."""
    return asyncio.run(_fan_out_backfill(field_id))


@celery.task(name="collection.collect_pass")
def collect_pass_task(field_id: str, scene_id: str, pass_date: str) -> dict[str, object]:
    """Collect one backfill pass (a single scene) for a field under its per-scene lock (D11)."""
    return asyncio.run(_collect_one_pass(field_id, scene_id, pass_date))


@celery.task(name="collection.forward_fill_field")
def forward_fill_field(field_id: str) -> dict[str, object]:
    """Collect a field's new passes since its cursor (forward-fill cadence)."""
    return asyncio.run(_run_for_field(field_id, is_backfill=False))


@celery.task(name="collection.scan_and_enqueue")
def scan_and_enqueue() -> dict[str, int]:
    """Beat entrypoint: enqueue backfill for flagged fields and forward-fill for due ones."""
    return asyncio.run(_scan_and_enqueue())


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


async def _analyse_aoi(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Compute one index over an arbitrary AOI for its most recent usably-clear pass, without
    persisting anything. Only the few most-recent scenes are fetched (not the whole window) so a
    single click never fans out to dozens of reads, and the clearest of them is returned."""
    settings = get_settings()
    adapter = get_access_adapter(settings)
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    spec = get_index(index_name)
    now = datetime.now(UTC)
    scenes = await adapter.search(
        aoi, TimeRange(start=now - timedelta(days=90), end=now), max_scene_cloud_pct=70.0
    )
    if not scenes:
        return {"status": "no_scenes"}

    bands = sorted(spec.bands)
    best_scene = None
    best_out = None
    for scene in sorted(scenes, key=lambda s: s.sensing_datetime, reverse=True)[:3]:
        fetched = await adapter.fetch(
            scene, aoi, bands=bands, resolution_m=float(spec.resolution_m)
        )
        out = analyze_index(
            reflectance=fetched.data.bands,
            index_name=index_name,
            resolution_m=int(fetched.data.resolution_m),
            clear_fraction_override=fetched.clear_fraction,
        )
        if best_out is None or out.clear_fraction > best_out.clear_fraction:
            best_scene, best_out = scene, out
        if out.clear_fraction >= 0.6:  # clear enough; stop early to stay responsive
            break

    assert best_scene is not None and best_out is not None
    s = best_out.stats
    return {
        "status": "ok",
        "index": best_out.index_name,
        "pass_date": best_scene.sensing_datetime.date().isoformat(),
        "scene_id": best_scene.scene_id,
        "mean": s.mean,
        "min": s.min,
        "max": s.max,
        "p10": s.p10,
        "p90": s.p90,
        "clear_fraction": best_out.clear_fraction,
        "confidence": best_out.confidence,
        "resolution_m": best_out.resolution_m,
        "pixels": s.count,
    }


@celery.task(name="analysis.analyse_aoi")
def analyse_aoi_task(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Ad-hoc preview analysis over a custom AOI (the workspace "analyse this area" action): the
    most recent usable pass's index stats, computed through the same engine as stored analyses but
    never persisted. No field is created, so it cannot collide with gateway-owned identity
    (invariant 6)."""
    return asyncio.run(_analyse_aoi(geometry, index_name))
