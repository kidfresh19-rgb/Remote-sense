"""Live collection (Phase 3, D4-live): the backfill and forward-fill that wire the planning
kernel, the lock-gated collection, and DB persistence into one resumable unit of work, plus the
beat scan that enqueues them."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import redis.asyncio as aioredis
from geoalchemy2.shape import to_shape
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
from rs_core.cache import RedisJsonCache, redis_json_cache_from_settings
from rs_core.config import ImageryAdapter, Settings
from rs_core.logging import get_logger
from rs_imagery import AOI, AccessPort, SceneRef, TimeRange, get_access_adapter
from shapely.geometry import mapping
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.worker.celery_app import celery
from services.worker.collection import collect_field_locked
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

log = get_logger("services.worker.tasks.collection")

# The core indices stored on every usable pass (PLAN §5). Adding one is a config change here, not
# a schema migration - the analysis row is index-agnostic.
CORE_INDICES = ["ndvi", "evi2", "savi", "ndre", "ndmi"]

# Targeted "collect specific dates": how far a requested calendar date may snap to find a real pass.
# Sentinel-2 revisits ~every 5 days, so one revisit cycle either side resolves almost any date.
# The batch-size cap is enforced at the API (services/api/workspace/fields.py).
COLLECT_DATES_TOLERANCE_DAYS = 7


@dataclass(frozen=True)
class CollectionSummary:
    """Outcome of one field collection run. `locked` means another worker held the unit (R-1) and
    this run did nothing."""

    locked: bool
    scenes: int
    analyses: int
    cursor_date: date | None = None
    band_memo_hits: int | None = None
    band_memo_misses: int | None = None


def _summary_dict(summary: CollectionSummary) -> dict[str, object]:
    """JSON-safe form for a Celery result (date -> isoformat)."""
    return {
        "locked": summary.locked,
        "scenes": summary.scenes,
        "analyses": summary.analyses,
        "cursor_date": summary.cursor_date.isoformat() if summary.cursor_date else None,
        "band_memo_hits": summary.band_memo_hits,
        "band_memo_misses": summary.band_memo_misses,
    }


def _adapter_read_stats(adapter: AccessPort) -> dict[str, int] | None:
    """The adapter's per-task band/metadata memo hit/miss counts, or None when the active adapter
    has no read-level memo (mock, server_compute). `misses` is the real CDSE read count - the
    per-pass read budget observable that Phase 4 is gated on. Read defensively via getattr so the
    engine stays adapter-agnostic (invariant 1)."""
    getter = getattr(adapter, "read_cache_stats", None)
    if getter is None:
        return None
    stats = getter()
    return stats if isinstance(stats, dict) else None


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
    scenes: Sequence[SceneRef] | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> CollectionSummary:
    """The body both collection tasks share: collect a field over a time range under its enqueue
    lock, persist every result (scene metadata + per-index zonal stats), and advance the cursor.
    When `cog_store` is given, also emit + store each index COG for the tiler (D1/D7). `scenes` is
    forwarded to the known-scene fast path (a fanned-out pass already holds its scene ref). Returns
    a summary with band-memo stats for the caller to log; `locked=True` means another worker held
    the unit and we did nothing (R-1). Runs inside the caller's transaction - the caller commits."""
    results = await collect_field_locked(
        lock_client=lock_client,
        collection_key=collection_key,
        adapter=adapter,
        aoi=aoi,
        time_range=time_range,
        indices=indices,
        already_processed=already_processed,
        emit_rasters=cog_store is not None,
        scenes=scenes,
        ttl_seconds=ttl_seconds,
    )
    if results is None:
        return CollectionSummary(locked=True, scenes=0, analyses=0)

    latest_pass: date | None = None
    latest_scene: str | None = None
    analyses = 0
    for result in results:
        # Scene metadata first: the analysis rows FK onto it (first-write-wins, immutable).
        # scene_metadata travels on each ScenePassResult from collect_field (read per scene from
        # metadata, invariant 2), so no second adapter.metadata() call is needed here.
        await upsert_scene_metadata(
            session,
            scene_id=result.scene_id,
            provider=result.provider,
            quantification_value=result.scene_metadata.quantification_value,
            boa_add_offset=result.scene_metadata.boa_add_offset,
            crs=result.scene_metadata.crs,
            sensing_datetime=result.sensing_datetime,
            processing_baseline=result.scene_metadata.processing_baseline,
            scene_cloud_pct=result.scene_metadata.scene_cloud_pct,
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
    band_stats = _adapter_read_stats(adapter)
    return CollectionSummary(
        locked=False,
        scenes=len(results),
        analyses=analyses,
        cursor_date=latest_pass,
        band_memo_hits=band_stats["hits"] if band_stats is not None else None,
        band_memo_misses=band_stats["misses"] if band_stats is not None else None,
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
) -> list[SceneRef]:
    """The not-yet-processed passes in the field's backfill window, as the scene refs to fan one
    task out per pass (D11). Dedups against the scenes already stored for this field at this
    geometry version (R-1), so a re-scan enqueues only what is missing - which is also how the
    backfill converges: an empty plan means the window is fully collected. Returning the full
    `SceneRef` (not just the id) lets the fan-out thread it to the pass task, so the pass skips its
    own one-day search and keeps the real `sensing_datetime` for provenance (invariant 5)."""
    window_start, _ = backfill_window(now.date(), months)
    scenes = await adapter.search(aoi, TimeRange(start=_start_of_day(window_start), end=now))
    already = await processed_scene_ids(
        session, field_id=field_id, geometry_version=geometry_version
    )
    by_id = {s.scene_id: s for s in scenes}
    return [by_id[scene_id] for scene_id in plan_scenes([s.scene_id for s in scenes], already)]


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
    scene_ref: SceneRef | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> CollectionSummary:
    """Collect a single backfill pass (one scene) under its per-scene lock (R-1, D11). When
    `scene_ref` is supplied (the fan-out threads it from its window search), the known-scene fast
    path skips this pass's own one-day `adapter.search`; the one-day window is still built as the
    fallback bound if the scene-item cache is cold. Persistence and the cursor advance reuse
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
        scenes=[scene_ref] if scene_ref is not None else None,
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


def build_windowed_cog_reader(
    settings: Settings, *, search_cache: RedisJsonCache | None = None
) -> tuple[AccessPort, list[RedisJsonCache]]:
    """Build the imagery adapter plus the scene caches to close at task end - the single place that
    wires the windowed_cog cache set, so the stored-collection path and the AOI Studio path cannot
    drift. For windowed_cog, attach the two immutable cross-lane caches keyed by the scene id: the
    scene-metadata cache (any reader sharing a Sentinel-2 tile skips the product-XML read after the
    first pass) and the scene-item cache (a pass resolves asset hrefs from Redis instead of
    re-running a one-day STAC search). When `search_cache` is given (the AOI Studio path), it is
    wired too but owned by the caller, so only the scene caches built here are returned to close. A
    scene touched by either lane warms the other - one read per scene across the system. Any other
    adapter (mock / server_compute) is returned unchanged with no caches - the adapter stays a
    config switch (invariant 1). Invariant 2 holds: each cached value was itself read per scene from
    metadata; every cache fails open, so a Redis hiccup degrades to a live read, never an error."""
    if settings.imagery_adapter is not ImageryAdapter.WINDOWED_COG:
        return get_access_adapter(settings), []
    from rs_imagery.adapters.windowed_cog import WindowedCogAdapter

    scene_meta_cache = redis_json_cache_from_settings(settings, namespace="cdse:scene_meta")
    scene_item_cache = redis_json_cache_from_settings(settings, namespace="cdse:scene_item")
    adapter = WindowedCogAdapter(
        settings,
        search_cache=search_cache,
        scene_meta_cache=scene_meta_cache,
        scene_item_cache=scene_item_cache,
    )
    caches = [c for c in (scene_meta_cache, scene_item_cache) if c is not None]
    return adapter, caches


def _build_collection_adapter(settings: Settings) -> tuple[AccessPort, list[RedisJsonCache]]:
    """The imagery adapter for the stored backfill / forward-fill path: the shared windowed_cog
    reader with the immutable scene caches but no AOI search cache (the stored path dedups against
    the DB, not a search cache). See build_windowed_cog_reader."""
    return build_windowed_cog_reader(settings)


async def _aclose_caches(caches: list[RedisJsonCache]) -> None:
    """Close each task-scoped cache's Redis client. Fail-open is the cache's own contract, so a
    close error is swallowed there; this just drives the loop at task teardown."""
    for cache in caches:
        await cache.aclose()


async def _run_for_field(field_id: str, *, is_backfill: bool) -> dict[str, object]:
    settings = get_settings()
    adapter, caches = _build_collection_adapter(settings)
    redis = aioredis.Redis.from_url(settings.redis_url)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    t0 = time.monotonic()
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
        log.info(
            "collection.field.complete",
            field_id=field_id,
            is_backfill=is_backfill,
            scenes=summary.scenes,
            analyses=summary.analyses,
            locked=summary.locked,
            wall_clock_s=round(time.monotonic() - t0, 2),
            band_memo_hits=summary.band_memo_hits,
            band_memo_misses=summary.band_memo_misses,
        )
        return _summary_dict(summary)
    finally:
        await _aclose_caches(caches)
        await redis.aclose()
        await engine.dispose()


def _split_passes_by_lane(
    plan: Sequence[SceneRef], *, interactive: bool, head: int
) -> tuple[list[SceneRef], list[SceneRef]]:
    """Partition fanned-out backfill passes into `(interactive, bulk)`. A non-interactive run (the
    nightly sweep / ingest) keeps every pass on the bulk lane in discovery order. A user-initiated
    run promotes the `head` most-recent passes to the interactive lane: `plan_backfill_scenes`
    returns scenes oldest-first, so sort newest-first before taking the head, or the *oldest* passes
    would be the ones promoted. A plan of `head` or fewer passes goes entirely interactive. Pure (no
    I/O) so the ordering rule is unit-testable with no DB (CLAUDE.md 3)."""
    if not interactive:
        return [], list(plan)
    ordered = sorted(plan, key=lambda r: r.sensing_datetime, reverse=True)
    return ordered[:head], ordered[head:]


def _enqueue_collect_pass(field_id: str, ref: SceneRef, *, queue: str | None = None) -> None:
    """Enqueue one fanned-out backfill pass for `ref`. Thread the serialised SceneRef so the pass
    skips its own one-day search (2b) and keeps the real sensing_datetime; scene_id + pass_date
    stay positional so an in-flight old-format message (acks_late redelivery) still deserialises and
    takes the search fallback. `queue=None` routes to the task's default bulk `celery` lane;
    "interactive" puts a user-initiated pass on the reserved lane, clear of the daily sweep."""
    args = [
        field_id,
        ref.scene_id,
        ref.sensing_datetime.date().isoformat(),
        ref.model_dump_json(),
    ]
    if queue is None:
        collect_pass_task.delay(*args)
    else:
        collect_pass_task.apply_async(args=args, queue=queue)


async def _fan_out_backfill(field_id: str, *, interactive: bool = False) -> dict[str, object]:
    """Plan the field's outstanding backfill passes and enqueue one collect_pass task per pass; if
    none remain, the window is fully collected, so mark the backfill complete and clear the flag
    (D11). Each pass then runs and commits independently under its own per-scene lock.

    `interactive` is set only by the user-initiated "Collect now" endpoint: the newest
    `collect_now_interactive_head` passes are routed to the `interactive` lane so the field's
    current state populates promptly, while the deep-history tail stays on the bulk lane and never
    crowds the AOI Studio previews that share the interactive lane. The nightly sweep and ingest
    leave it False, so every pass runs on the bulk lane exactly as before."""
    settings = get_settings()
    adapter, caches = _build_collection_adapter(settings)
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
        await _aclose_caches(caches)
        await engine.dispose()
    if not plan:
        return {"fanned_out": 0, "interactive": 0, "complete": True}

    interactive_passes, bulk_passes = _split_passes_by_lane(
        plan, interactive=interactive, head=settings.collect_now_interactive_head
    )
    for ref in interactive_passes:
        _enqueue_collect_pass(field_id, ref, queue="interactive")
    for ref in bulk_passes:
        _enqueue_collect_pass(field_id, ref)
    log.info(
        "collection.backfill.fanned_out",
        field_id=field_id,
        interactive=interactive,
        fanned_out=len(plan),
        interactive_passes=len(interactive_passes),
        bulk_passes=len(bulk_passes),
    )
    return {
        "fanned_out": len(plan),
        "interactive": len(interactive_passes),
        "complete": False,
    }


def _snap_dates(
    requested: list[date],
    scenes: list[SceneRef],
    already: frozenset[str],
    *,
    tolerance_days: int = COLLECT_DATES_TOLERANCE_DAYS,
) -> tuple[list[dict[str, object]], list[str], dict[str, str]]:
    """Snap each requested calendar date to the nearest scene within +/-`tolerance_days` (closest
    |gap| wins; a tie goes to the earlier acquisition, i.e. on-or-before). Dedup scene ids across
    dates and drop any already stored for this field+geometry version. Returns
    `(resolved, skipped, to_enqueue)`: `resolved` is one entry per requested date that found a pass
    (with the signed `day_gap`), `skipped` the dates with no pass in range, and `to_enqueue` maps
    the new `scene_id -> pass_date` to collect. Pure - no DB, no network - so the snapping is
    testable against synthetic scenes (CLAUDE.md 3)."""
    by_date = sorted((s.sensing_datetime.date(), s.scene_id) for s in scenes)
    resolved: list[dict[str, object]] = []
    skipped: list[str] = []
    to_enqueue: dict[str, str] = {}
    for day in sorted(set(requested)):
        best: tuple[int, date, str] | None = None  # (abs gap, scene date, scene id)
        for scene_date, scene_id in by_date:
            gap = abs((scene_date - day).days)
            if gap <= tolerance_days:
                candidate = (gap, scene_date, scene_id)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            skipped.append(day.isoformat())
            continue
        _, scene_date, scene_id = best
        resolved.append(
            {
                "requested_date": day.isoformat(),
                "scene_id": scene_id,
                "pass_date": scene_date.isoformat(),
                "day_gap": (scene_date - day).days,
            }
        )
        if scene_id not in already:
            to_enqueue.setdefault(scene_id, scene_date.isoformat())
    return resolved, skipped, to_enqueue


async def _plan_collect_dates(field_id: str, dates: list[str]) -> dict[str, object]:
    """Resolve a batch of requested dates to a field's nearest passes (+/-7 days, deduped) and fan
    out one `collect_pass` per new scene - the targeted-dates sibling of `_fan_out_backfill`. One
    archive search spans the whole batch (with the tolerance padded on each end); the snapping is
    pure (`_snap_dates`). Returns a per-date resolution summary; the collection itself runs async in
    the fanned-out tasks. Nothing new is invented - a snapped pass is a real scene collected exactly
    like a backfill pass."""
    settings = get_settings()
    adapter, caches = _build_collection_adapter(settings)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            field = (
                await session.execute(select(Field).where(Field.id == uuid.UUID(field_id)))
            ).scalar_one()
            geometry_version = field.geometry_version
            aoi = field_to_aoi(field)
            requested = sorted({date.fromisoformat(d) for d in dates})
            window = TimeRange(
                start=_start_of_day(requested[0] - timedelta(days=COLLECT_DATES_TOLERANCE_DAYS)),
                end=_start_of_day(requested[-1] + timedelta(days=COLLECT_DATES_TOLERANCE_DAYS))
                + timedelta(days=1),
            )
            scenes = await adapter.search(aoi, window)
            already = await processed_scene_ids(
                session, field_id=field.id, geometry_version=geometry_version
            )
    finally:
        await _aclose_caches(caches)
        await engine.dispose()

    resolved, skipped, to_enqueue = _snap_dates(requested, scenes, already)
    by_id = {s.scene_id: s for s in scenes}
    for scene_id, pass_date in to_enqueue.items():
        # User-initiated collection: run on the interactive lane (queue isolation, celery_app.py)
        # so a requested date is collected promptly instead of queuing behind the bulk backfill,
        # whose collect_pass fan-out (_fan_out_backfill) stays on the default queue. Thread the
        # scene ref (already in hand from this search) so the pass skips its own one-day search.
        collect_pass_task.apply_async(
            args=[field_id, scene_id, pass_date, by_id[scene_id].model_dump_json()],
            queue="interactive",
        )
    return {
        "field_id": field_id,
        "requested": len(requested),
        "resolved": resolved,
        "skipped": skipped,
        "enqueued": len(to_enqueue),
    }


async def _collect_one_pass(
    field_id: str, scene_id: str, pass_date: str, scene_ref: str | None = None
) -> dict[str, object]:
    settings = get_settings()
    adapter, caches = _build_collection_adapter(settings)
    redis = aioredis.Redis.from_url(settings.redis_url)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    # The fan-out serialises the neutral SceneRef (no vendor type leaks past the adapter); an
    # old-format in-flight message has none, so the pass falls back to its one-day search.
    ref = SceneRef.model_validate_json(scene_ref) if scene_ref is not None else None
    t0 = time.monotonic()
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
                scene_ref=ref,
            )
            await session.commit()
        log.info(
            "collection.pass.complete",
            field_id=field_id,
            scene_id=scene_id,
            pass_date=pass_date,
            scenes=summary.scenes,
            analyses=summary.analyses,
            locked=summary.locked,
            wall_clock_s=round(time.monotonic() - t0, 2),
            band_memo_hits=summary.band_memo_hits,
            band_memo_misses=summary.band_memo_misses,
        )
        return _summary_dict(summary)
    finally:
        await _aclose_caches(caches)
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
def backfill_field(field_id: str, interactive: bool = False) -> dict[str, object]:
    """Fan a field's full-history backfill into one collect_pass task per outstanding pass (D11);
    a later scan that finds no passes left converges it to backfill-complete. `interactive` is the
    optional trailing flag the user-initiated "Collect now" endpoint sets so the newest passes fan
    out on the interactive lane; the nightly sweep and ingest omit it (bulk lane). It is the last
    arg so an in-flight 1-arg message (acks_late redelivery) still runs and defaults to bulk."""
    return asyncio.run(_fan_out_backfill(field_id, interactive=interactive))


@celery.task(name="collection.collect_pass")
def collect_pass_task(
    field_id: str, scene_id: str, pass_date: str, scene_ref: str | None = None
) -> dict[str, object]:
    """Collect one backfill pass (a single scene) for a field under its per-scene lock (D11).
    `scene_ref` (serialised SceneRef) is the 2b fast-path hint that lets the pass skip its own STAC
    search; it is the optional trailing arg so an in-flight 3-arg message still runs (search
    fallback)."""
    return asyncio.run(_collect_one_pass(field_id, scene_id, pass_date, scene_ref))


@celery.task(name="collection.collect_dates_field")
def collect_dates_field(field_id: str, dates: list[str]) -> dict[str, object]:
    """Targeted "collect specific dates": snap a batch of requested dates to the field's nearest
    passes (+/-7 days, deduped) and fan out one `collect_pass` per new scene. The targeted-dates
    sibling of `backfill_field`; each collected pass persists exactly like a backfill pass and so
    becomes pushable through the existing per-farm publish."""
    return asyncio.run(_plan_collect_dates(field_id, dates))


@celery.task(name="collection.forward_fill_field")
def forward_fill_field(field_id: str) -> dict[str, object]:
    """Collect a field's new passes since its cursor (forward-fill cadence)."""
    return asyncio.run(_run_for_field(field_id, is_backfill=False))


@celery.task(name="collection.scan_and_enqueue")
def scan_and_enqueue() -> dict[str, int]:
    """Beat entrypoint: enqueue backfill for flagged fields and forward-fill for due ones."""
    return asyncio.run(_scan_and_enqueue())
