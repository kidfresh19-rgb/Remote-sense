"""Persistence helpers that span requests/tasks rather than belonging to one endpoint.

Phase 1 ships the scene-metadata upsert. Per-scene reflectance metadata (the quantification
value + BOA offset) is global to the system and immutable once stored: it is a fixed property
of the scene, and index math reads it from here rather than hard-coding it (CLAUDE.md
invariant 2). The upsert is therefore keyed by scene_id and first-write-wins - a later, diverging
value is NOT silently overwritten; the caller can detect it via the returned `created` flag.

Phase 2/3 adds the analysis upsert (D3): one engine result becomes one additive, idempotent
`analysis` row keyed by its scientific identity (field, scene, index, geometry_version,
formula_version). Re-processing the same identity refreshes that row in place rather than
duplicating it, so a re-run can complete a partial write or attach a COG uri later.

Kept value-based (no rs_imagery / rs_analysis import) so rs_core stays free of an upward
dependency on the imagery or analysis layers; the worker maps an AccessPort SceneMetadata or an
engine AnalysisOutput onto these arguments at call time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TypedDict

from sqlalchemy import Insert, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import (
    Analysis,
    Annotation,
    Farm,
    Field,
    FieldCollectionState,
    FieldGeometryVersion,
    Interpretation,
    SceneMetadata,
    SyncOutbox,
)


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


# The analysis identity (PLAN §5): one row per field/scene/index at a given geometry + formula
# version. Re-processing the same identity must converge, never duplicate, so the upsert keys on
# this constraint and refreshes only the computed payload + provenance listed here.
_ANALYSIS_MUTABLE = (
    "pass_date",
    "mean",
    "min_val",
    "max_val",
    "std",
    "p10",
    "p90",
    "clear_fraction",
    "resolution_m",
    "provider",
    "provider_scene_id",
    "processing_mode",
    "cog_uri",
    "confidence",
)


def _analysis_upsert_stmt(values: dict[str, object]) -> Insert:
    """Build the INSERT ... ON CONFLICT DO UPDATE for one analysis row. Factored out so the value
    mapping and the conflict target are unit-testable with no database. RETURNING `(xmax = 0)`
    reports whether this call inserted (True) or refreshed an existing row (False) - the standard
    Postgres idiom for telling the two apart in a single statement."""
    stmt = pg_insert(Analysis).values(**values)
    return stmt.on_conflict_do_update(
        constraint="uq_analysis_identity",
        set_={col: stmt.excluded[col] for col in _ANALYSIS_MUTABLE},
    ).returning(literal_column("(xmax = 0)"))


async def upsert_analysis(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    index_name: str,
    formula_version: str,
    geometry_version: int,
    provider: str,
    provider_scene_id: str,
    processing_mode: str,
    resolution_m: float,
    clear_fraction: float,
    mean: float | None = None,
    min_val: float | None = None,
    max_val: float | None = None,
    std: float | None = None,
    p10: float | None = None,
    p90: float | None = None,
    confidence: str | None = None,
    cog_uri: str | None = None,
) -> tuple[Analysis, bool]:
    """Persist one engine result as an `analysis` row; return (row, created). Additive and
    idempotent (CLAUDE.md invariant 5): a new scientific identity inserts, a repeat refreshes the
    same row in place rather than duplicating it. Every row carries the full provenance tuple
    (provider, provider_scene_id, processing_mode, formula_version, geometry_version)."""
    values: dict[str, object] = {
        "field_id": field_id,
        "scene_id": scene_id,
        "pass_date": pass_date,
        "index_name": index_name,
        "formula_version": formula_version,
        "geometry_version": geometry_version,
        "provider": provider,
        "provider_scene_id": provider_scene_id,
        "processing_mode": processing_mode,
        "resolution_m": resolution_m,
        "clear_fraction": clear_fraction,
        "mean": mean,
        "min_val": min_val,
        "max_val": max_val,
        "std": std,
        "p10": p10,
        "p90": p90,
        "confidence": confidence,
        "cog_uri": cog_uri,
    }
    created = bool((await session.execute(_analysis_upsert_stmt(values))).scalar_one())
    row = (
        await session.execute(
            select(Analysis).where(
                Analysis.field_id == field_id,
                Analysis.scene_id == scene_id,
                Analysis.index_name == index_name,
                Analysis.geometry_version == geometry_version,
                Analysis.formula_version == formula_version,
            )
        )
    ).scalar_one()
    return row, created


def advance_cursor(current: date | None, candidate: date | None) -> date | None:
    """The forward-fill date watermark only ever moves forward (D5). Return the later of the
    stored cursor and a candidate pass date: a missing value yields the other, and an earlier
    candidate is ignored so a late-arriving older scene can never rewind the cursor. The single
    home for this rule, used by the cursor-persistence helpers below and by the worker when it
    plans the next search start."""
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


async def ensure_collection_state(
    session: AsyncSession, *, field_id: uuid.UUID, geometry_version: int
) -> FieldCollectionState:
    """Get-or-create the collection cursor for a field at a geometry version. Concurrency-safe: a
    racing creator hits ON CONFLICT DO NOTHING, then both readers see the one row."""
    await session.execute(
        pg_insert(FieldCollectionState)
        .values(field_id=field_id, geometry_version=geometry_version, backfill_complete=False)
        .on_conflict_do_nothing(constraint="uq_collection_state_field_geom")
    )
    return (
        await session.execute(
            select(FieldCollectionState).where(
                FieldCollectionState.field_id == field_id,
                FieldCollectionState.geometry_version == geometry_version,
            )
        )
    ).scalar_one()


async def get_collection_state(
    session: AsyncSession, *, field_id: uuid.UUID, geometry_version: int
) -> FieldCollectionState | None:
    """The collection cursor for a field at a geometry version, or None if collection has not
    started for it yet."""
    return (
        await session.execute(
            select(FieldCollectionState).where(
                FieldCollectionState.field_id == field_id,
                FieldCollectionState.geometry_version == geometry_version,
            )
        )
    ).scalar_one_or_none()


async def record_forward_fill_poll(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    polled_at: datetime,
    cursor_date: date | None = None,
    last_scene_id: str | None = None,
) -> FieldCollectionState:
    """Record a forward-fill poll: stamp `last_poll_at`, and advance the date watermark only
    forward (never backward, via `advance_cursor`). Creates the cursor row if absent."""
    state = await ensure_collection_state(
        session, field_id=field_id, geometry_version=geometry_version
    )
    state.last_poll_at = polled_at
    advanced = advance_cursor(state.cursor_date, cursor_date)
    if advanced != state.cursor_date:
        state.cursor_date = advanced
        if last_scene_id is not None:
            state.last_scene_id = last_scene_id
    await session.flush()
    return state


async def mark_backfill_complete(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    completed_at: datetime,
    cursor_date: date | None = None,
) -> FieldCollectionState:
    """Flag the historical backfill done for this field+geometry version and stamp the completion
    time; optionally seed the forward-fill watermark with the latest backfilled pass date."""
    state = await ensure_collection_state(
        session, field_id=field_id, geometry_version=geometry_version
    )
    state.backfill_complete = True
    state.backfill_completed_at = completed_at
    # The backfill is itself the most recent archive poll, so the first forward-fill is due one
    # cadence later rather than immediately.
    state.last_poll_at = completed_at
    advanced = advance_cursor(state.cursor_date, cursor_date)
    if advanced != state.cursor_date:
        state.cursor_date = advanced
    await session.flush()
    return state


async def processed_scene_ids(
    session: AsyncSession, *, field_id: uuid.UUID, geometry_version: int
) -> frozenset[str]:
    """The scene ids already analysed for this field at this geometry version - the dedup set the
    pipeline hands to plan_scenes so a resumed or retried run skips finished scenes (R-1) and
    fills only the gaps. Derived from the analysis rows themselves, so it can never drift from
    what was actually stored."""
    rows = await session.execute(
        select(Analysis.scene_id)
        .where(
            Analysis.field_id == field_id,
            Analysis.geometry_version == geometry_version,
        )
        .distinct()
    )
    return frozenset(rows.scalars().all())


async def insert_interpretation(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    geometry_version: int,
    prompt_version: str,
    crop: str | None,
    narrative: str,
    status: str,
    confidence: str,
    model: str,
) -> tuple[Interpretation, bool]:
    """Store a drafted interpretation if none exists for this field/pass/geometry/prompt-version;
    return (row, created). First-draft-wins (ON CONFLICT DO NOTHING), so a re-run never clobbers a
    read an agronomist may already be reviewing. Always stored unpublished + needs_review (risk
    #6); a new prompt_version is a fresh identity and a fresh draft."""
    stmt = (
        pg_insert(Interpretation)
        .values(
            field_id=field_id,
            scene_id=scene_id,
            pass_date=pass_date,
            geometry_version=geometry_version,
            prompt_version=prompt_version,
            crop=crop,
            narrative=narrative,
            status=status,
            confidence=confidence,
            model=model,
            needs_review=True,
            published=False,
        )
        .on_conflict_do_nothing(constraint="uq_interpretation_identity")
        .returning(Interpretation.id)
    )
    created = (await session.execute(stmt)).scalar_one_or_none() is not None
    row = (
        await session.execute(
            select(Interpretation).where(
                Interpretation.field_id == field_id,
                Interpretation.scene_id == scene_id,
                Interpretation.geometry_version == geometry_version,
                Interpretation.prompt_version == prompt_version,
            )
        )
    ).scalar_one()
    return row, created


async def get_interpretation(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    scene_id: str,
    geometry_version: int,
    prompt_version: str,
) -> Interpretation | None:
    """The stored interpretation for a field/pass at a geometry + prompt version, or None. Lets
    the worker skip a field/pass it has already drafted (and avoid a wasted model call)."""
    return (
        await session.execute(
            select(Interpretation).where(
                Interpretation.field_id == field_id,
                Interpretation.scene_id == scene_id,
                Interpretation.geometry_version == geometry_version,
                Interpretation.prompt_version == prompt_version,
            )
        )
    ).scalar_one_or_none()


async def get_interpretation_by_id(
    session: AsyncSession, *, interpretation_id: uuid.UUID, field_id: uuid.UUID
) -> Interpretation | None:
    """One interpretation by id, scoped to its field so a stale id from another field can never
    match (the workspace review action carries both ids)."""
    return (
        await session.execute(
            select(Interpretation).where(
                Interpretation.id == interpretation_id,
                Interpretation.field_id == field_id,
            )
        )
    ).scalar_one_or_none()


async def review_interpretation(
    session: AsyncSession,
    *,
    interpretation_id: uuid.UUID,
    field_id: uuid.UUID,
    reviewer: str,
    publish: bool,
    narrative: str | None = None,
    now: datetime | None = None,
) -> Interpretation | None:
    """Record an agronomist's review of a drafted read (risk #6, the only path that can publish
    one). Stamps `reviewed_by` + `reviewed_at`, clears `needs_review`, and sets `published` per the
    action (publish / unpublish). An optional `narrative` replaces the model's draft text, so a
    reviewer can correct a hallucination before it reaches anyone. `status` and `confidence` are
    grounded in the zonal stats, not the model, so they are immutable here. Scoped to `field_id`;
    returns the updated row, or None if no such interpretation."""
    row = await get_interpretation_by_id(
        session, interpretation_id=interpretation_id, field_id=field_id
    )
    if row is None:
        return None
    if narrative is not None:
        row.narrative = narrative
    row.published = publish
    row.needs_review = False
    row.reviewed_by = reviewer
    row.reviewed_at = now or datetime.now(UTC)
    await session.flush()
    return row


async def published_narratives_for_farm(
    session: AsyncSession, canonical_farm_id: str
) -> list[tuple[str | None, date, str]]:
    """Each published interpretation for a farm as `(canonical_field_id, pass_date, narrative)`,
    latest-reviewed winning per (field, pass). Drives the gateway's interpretation block, gated on
    `published` so an unreviewed read never reaches the wire (risk #6 extended outbound). Geometry
    is never selected (invariant 6)."""
    rows = (
        await session.execute(
            select(
                Field.canonical_field_id,
                Interpretation.pass_date,
                Interpretation.narrative,
            )
            .join(Field, Interpretation.field_id == Field.id)
            .join(Farm, Field.farm_id == Farm.id)
            .where(
                Farm.canonical_farm_id == canonical_farm_id,
                Interpretation.published.is_(True),
            )
            # Ascending review time so the most recently reviewed read overwrites in the dict below.
            .order_by(Interpretation.reviewed_at.asc().nulls_first())
        )
    ).all()
    latest: dict[tuple[str | None, date], str] = {}
    for canonical_field_id, pass_date, narrative in rows:
        latest[(canonical_field_id, pass_date)] = narrative
    return [(cfid, pass_date, narrative) for (cfid, pass_date), narrative in latest.items()]


async def list_review_queue(
    session: AsyncSession, *, needs_review: bool | None = None, limit: int = 200
):
    """Interpretations across every field for the cross-field agronomist review surface, each with
    its field/farm canonical ids, name and crop (geometry-free). `needs_review=True` narrows to the
    unreviewed backlog; omitted returns all. Oldest pass first so the longest-waiting read is on
    top. Returns SQLAlchemy rows; the workspace layer shapes them into `ReviewQueueItem`."""
    stmt = (
        select(
            Interpretation.id,
            Interpretation.field_id,
            Field.canonical_field_id,
            Field.name.label("field_name"),
            Field.crop,
            Farm.canonical_farm_id,
            Interpretation.pass_date,
            Interpretation.status,
            Interpretation.confidence,
            Interpretation.narrative,
            Interpretation.needs_review,
            Interpretation.published,
            Interpretation.reviewed_by,
            Interpretation.reviewed_at,
        )
        .join(Field, Interpretation.field_id == Field.id)
        .join(Farm, Field.farm_id == Farm.id)
        .order_by(Interpretation.pass_date, Farm.canonical_farm_id, Field.canonical_field_id)
        .limit(limit)
    )
    if needs_review is not None:
        stmt = stmt.where(Interpretation.needs_review.is_(needs_review))
    return list((await session.execute(stmt)).all())


async def insert_annotation(
    session: AsyncSession,
    *,
    field_id: uuid.UUID,
    geometry_version: int,
    pass_date: date | None,
    body: str,
    author: str | None,
) -> Annotation:
    """Append a field note. Unlike interpretations these carry no identity constraint - an analyst
    may pin many notes to the same field/pass - so this is a plain additive insert. The flush pulls
    back the server-assigned `created_at` so the caller can return the full row."""
    row = Annotation(
        field_id=field_id,
        geometry_version=geometry_version,
        pass_date=pass_date,
        body=body,
        author=author,
    )
    session.add(row)
    await session.flush()
    return row


async def list_annotations(session: AsyncSession, *, field_id: uuid.UUID) -> list[Annotation]:
    """Every note pinned to a field, newest first (id as a stable tiebreaker for notes that share
    a created_at)."""
    return list(
        (
            await session.execute(
                select(Annotation)
                .where(Annotation.field_id == field_id)
                .order_by(Annotation.created_at.desc(), Annotation.id)
            )
        )
        .scalars()
        .all()
    )


async def delete_annotation(
    session: AsyncSession, *, annotation_id: uuid.UUID, field_id: uuid.UUID
) -> bool:
    """Delete one note, scoped to its field so a stale id from another field can never match.
    Returns whether a row was removed (False -> 404 at the API)."""
    result = await session.execute(
        delete(Annotation).where(Annotation.id == annotation_id, Annotation.field_id == field_id)
    )
    return bool(result.rowcount)


async def get_outbox(session: AsyncSession, *, idempotency_key: str) -> SyncOutbox | None:
    """The outbox row for a push idempotency key, or None. Lets the publisher skip a payload it
    has already delivered (R-2)."""
    return (
        await session.execute(
            select(SyncOutbox).where(SyncOutbox.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()


async def _ensure_outbox(
    session: AsyncSession,
    *,
    idempotency_key: str,
    canonical_farm_id: str,
    payload_version: str,
    result_count: int,
) -> SyncOutbox:
    await session.execute(
        pg_insert(SyncOutbox)
        .values(
            idempotency_key=idempotency_key,
            canonical_farm_id=canonical_farm_id,
            payload_version=payload_version,
            result_count=result_count,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    return (
        await session.execute(
            select(SyncOutbox).where(SyncOutbox.idempotency_key == idempotency_key)
        )
    ).scalar_one()


async def record_push(
    session: AsyncSession,
    *,
    idempotency_key: str,
    canonical_farm_id: str,
    payload_version: str,
    result_count: int,
    ok: bool,
    detail: str | None,
    pushed_at: datetime,
) -> SyncOutbox:
    """Record the outcome of one gateway push. On success the row is `published`; on failure it is
    `dead_letter` (retryable, the error retained). Idempotent on the key - re-recording bumps
    `attempts` and updates the status, so a retried push converges rather than duplicating."""
    state = await _ensure_outbox(
        session,
        idempotency_key=idempotency_key,
        canonical_farm_id=canonical_farm_id,
        payload_version=payload_version,
        result_count=result_count,
    )
    state.attempts += 1
    if ok:
        state.status = "published"
        state.pushed_at = pushed_at
        state.last_error = None
    else:
        state.status = "dead_letter"
        state.last_error = detail
    await session.flush()
    return state


async def pipeline_health(session: AsyncSession) -> dict[str, int]:
    """A coverage + failure summary for the pipeline-health dashboard (R-4): total fields, fields
    still awaiting backfill, and dead-lettered gateway pushes awaiting retry."""
    fields = (await session.execute(select(func.count()).select_from(Field))).scalar_one()
    awaiting_backfill = (
        await session.execute(
            select(func.count()).select_from(Field).where(Field.needs_backfill.is_(True))
        )
    ).scalar_one()
    dead_letters = (
        await session.execute(
            select(func.count()).select_from(SyncOutbox).where(SyncOutbox.status == "dead_letter")
        )
    ).scalar_one()
    return {
        "fields": fields,
        "awaiting_backfill": awaiting_backfill,
        "dead_letters": dead_letters,
    }


async def get_latest_outbox_for_farm(
    session: AsyncSession, canonical_farm_id: str
) -> SyncOutbox | None:
    """The most recent outbox entry for a farm (by update time), or None if no push has ever been
    recorded. Used by the publish-status endpoint so the frontend can confirm delivery."""
    return (
        await session.execute(
            select(SyncOutbox)
            .where(SyncOutbox.canonical_farm_id == canonical_farm_id)
            .order_by(SyncOutbox.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


class FarmAnalyticsSummary(TypedDict):
    canonical_farm_id: str
    farm_name: str
    region: str | None
    total_fields: int
    total_area_hectares: float
    crops: list[str]
    latest_pass_date: date | None
    overall_health: str | None
    overall_health_score: float | None
    field_status_counts: dict[str, int]


class FarmAnalyticsTimeSeriesPoint(TypedDict):
    pass_date: date
    scene_id: str
    area_weighted_mean: float
    clear_fraction: float
    analyzed_fields: int
    analyzed_area_hectares: float
    health_distribution_pct: dict[str, float] | None


class FarmAnomaly(TypedDict):
    field_id: str
    canonical_field_id: str | None
    name: str | None
    crop: str | None
    field_area_hectares: float
    index_name: str
    field_value: float
    farm_average: float
    status: str
    anomaly_type: str
    detail: str


class FarmAnalyticsAnomalies(TypedDict):
    pass_date: date | None
    farm_average: float | None
    anomalies: list[FarmAnomaly]


async def get_farm_analytics_summary(
    session: AsyncSession, canonical_farm_id: str
) -> FarmAnalyticsSummary | None:
    """Calculate overall farm health summary on-the-fly."""
    farm_info = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if not farm_info:
        return None

    fields_data = (
        await session.execute(
            select(Field.id, Field.crop, FieldGeometryVersion.area_m2)
            .outerjoin(
                FieldGeometryVersion,
                (FieldGeometryVersion.field_id == Field.id)
                & (FieldGeometryVersion.version == Field.geometry_version),
            )
            .where(Field.farm_id == farm_info.id)
        )
    ).all()

    total_fields = len(fields_data)
    total_area_m2 = sum(row.area_m2 for row in fields_data if row.area_m2 is not None)
    crops = sorted(list(set(row.crop for row in fields_data if row.crop)))

    latest_pass_date = (
        await session.execute(
            select(func.max(Analysis.pass_date))
            .join(Field, Analysis.field_id == Field.id)
            .where(Field.farm_id == farm_info.id)
        )
    ).scalar()

    overall_health = None
    overall_health_score = None
    field_status_counts = {"healthy": 0, "moderate": 0, "stressed": 0, "critical": 0}

    if latest_pass_date:
        analyses = (
            await session.execute(
                select(
                    Analysis.mean,
                    Field.crop,
                    FieldGeometryVersion.area_m2,
                    Field.canonical_field_id,
                )
                .join(Field, Analysis.field_id == Field.id)
                .outerjoin(
                    FieldGeometryVersion,
                    (FieldGeometryVersion.field_id == Field.id)
                    & (FieldGeometryVersion.version == Field.geometry_version),
                )
                .where(
                    Field.farm_id == farm_info.id,
                    Analysis.pass_date == latest_pass_date,
                    Analysis.index_name == "ndvi",
                )
            )
        ).all()

        total_weight_area = 0.0
        weighted_ndvi_sum = 0.0
        from rs_interpret import classify, vigour_to_status

        for mean, crop, area_m2, _ in analyses:
            area = area_m2 if area_m2 is not None else 1.0
            if mean is not None:
                weighted_ndvi_sum += mean * area
                total_weight_area += area
                vigour_band = classify("ndvi", mean, crop).label
                status = vigour_to_status(vigour_band)
                field_status_counts[status] += 1

        if total_weight_area > 0:
            overall_health_score = round(weighted_ndvi_sum / total_weight_area, 4)
            farm_vigour = classify("ndvi", overall_health_score).label
            overall_health = vigour_to_status(farm_vigour)

    return {
        "canonical_farm_id": canonical_farm_id,
        "farm_name": farm_info.name,
        "region": farm_info.region,
        "total_fields": total_fields,
        "total_area_hectares": round(total_area_m2 / 10000.0, 2),
        "crops": crops,
        "latest_pass_date": latest_pass_date,
        "overall_health": overall_health,
        "overall_health_score": overall_health_score,
        "field_status_counts": field_status_counts,
    }


async def get_farm_analytics_timeseries(
    session: AsyncSession,
    canonical_farm_id: str,
    index: str = "ndvi",
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[FarmAnalyticsTimeSeriesPoint] | None:
    """Calculate farm-level index timeseries."""
    farm_info = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if not farm_info:
        return None

    stmt = (
        select(
            Analysis.pass_date,
            Analysis.scene_id,
            Analysis.mean,
            Analysis.clear_fraction,
            Field.crop,
            FieldGeometryVersion.area_m2,
        )
        .join(Field, Analysis.field_id == Field.id)
        .outerjoin(
            FieldGeometryVersion,
            (FieldGeometryVersion.field_id == Field.id)
            & (FieldGeometryVersion.version == Field.geometry_version),
        )
        .where(Field.farm_id == farm_info.id, Analysis.index_name == index)
    )
    if start_date:
        stmt = stmt.where(Analysis.pass_date >= start_date)
    if end_date:
        stmt = stmt.where(Analysis.pass_date <= end_date)

    stmt = stmt.order_by(Analysis.pass_date.asc())
    rows = (await session.execute(stmt)).all()

    from collections import defaultdict

    grouped = defaultdict(list)
    for r in rows:
        grouped[(r.pass_date, r.scene_id)].append(r)

    points = []
    from rs_interpret import classify, vigour_to_status

    for (pass_date, scene_id), records in sorted(grouped.items(), key=lambda x: x[0][0]):
        total_area = 0.0
        weighted_val_sum = 0.0
        weighted_clear_sum = 0.0
        status_areas = {"healthy": 0.0, "moderate": 0.0, "stressed": 0.0, "critical": 0.0}

        for r in records:
            area = r.area_m2 if r.area_m2 is not None else 1.0
            if r.mean is not None:
                weighted_val_sum += r.mean * area
                weighted_clear_sum += r.clear_fraction * area
                total_area += area

                if index.lower() == "ndvi":
                    vigour_band = classify("ndvi", r.mean, r.crop).label
                    status = vigour_to_status(vigour_band)
                    status_areas[status] += area

        if total_area > 0:
            distribution_pct = {}
            if index.lower() == "ndvi":
                for k, v in status_areas.items():
                    distribution_pct[k] = round((v / total_area) * 100.0, 1)
            else:
                distribution_pct = None

            points.append(
                {
                    "pass_date": pass_date,
                    "scene_id": scene_id,
                    "area_weighted_mean": round(weighted_val_sum / total_area, 4),
                    "clear_fraction": round(weighted_clear_sum / total_area, 4),
                    "analyzed_fields": len(records),
                    "analyzed_area_hectares": round(total_area / 10000.0, 2),
                    "health_distribution_pct": distribution_pct,
                }
            )
    return points


async def get_farm_analytics_anomalies(
    session: AsyncSession,
    canonical_farm_id: str,
    deviation_threshold: float = 0.15,
) -> FarmAnalyticsAnomalies | None:
    """Identify underperforming fields or fields with sudden biomass drops on the latest pass."""
    farm_info = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if not farm_info:
        return None

    latest_pass_date = (
        await session.execute(
            select(func.max(Analysis.pass_date))
            .join(Field, Analysis.field_id == Field.id)
            .where(Field.farm_id == farm_info.id)
        )
    ).scalar()

    if not latest_pass_date:
        return {"pass_date": None, "farm_average": None, "anomalies": []}

    analyses = (
        await session.execute(
            select(
                Analysis.field_id,
                Analysis.mean,
                Field.canonical_field_id,
                Field.name,
                Field.crop,
                FieldGeometryVersion.area_m2,
            )
            .join(Field, Analysis.field_id == Field.id)
            .outerjoin(
                FieldGeometryVersion,
                (FieldGeometryVersion.field_id == Field.id)
                & (FieldGeometryVersion.version == Field.geometry_version),
            )
            .where(
                Field.farm_id == farm_info.id,
                Analysis.pass_date == latest_pass_date,
                Analysis.index_name == "ndvi",
            )
        )
    ).all()

    total_area = 0.0
    weighted_ndvi_sum = 0.0
    for row in analyses:
        area = row.area_m2 if row.area_m2 is not None else 1.0
        if row.mean is not None:
            weighted_ndvi_sum += row.mean * area
            total_area += area

    farm_mean = weighted_ndvi_sum / total_area if total_area > 0 else 0.0

    anomalies = []
    from rs_interpret import classify, vigour_to_status

    for row in analyses:
        if row.mean is None:
            continue

        is_underperforming = row.mean < (farm_mean - deviation_threshold)

        previous_pass = (
            await session.execute(
                select(Analysis.mean, Analysis.pass_date)
                .where(
                    Analysis.field_id == row.field_id,
                    Analysis.index_name == "ndvi",
                    Analysis.pass_date < latest_pass_date,
                )
                .order_by(Analysis.pass_date.desc())
                .limit(1)
            )
        ).first()

        is_sudden_drop = False
        drop_detail = ""
        if previous_pass and previous_pass.mean is not None:
            drop = previous_pass.mean - row.mean
            if drop >= 0.2:
                is_sudden_drop = True
                drop_detail = (
                    f"NDVI dropped by {round(drop, 2)} from "
                    f"{round(previous_pass.mean, 2)} on {previous_pass.pass_date.isoformat()}"
                )

        vigour_band = classify("ndvi", row.mean, row.crop).label
        status = vigour_to_status(vigour_band)

        if is_underperforming or is_sudden_drop:
            anomaly_types = []
            details = []
            if is_underperforming:
                anomaly_types.append("underperforming")
                details.append(
                    f"NDVI is {round(farm_mean - row.mean, 2)} below farm "
                    f"average ({round(farm_mean, 2)})"
                )
            if is_sudden_drop:
                anomaly_types.append("sudden_drop")
                details.append(drop_detail)

            anomalies.append(
                {
                    "field_id": str(row.field_id),
                    "canonical_field_id": row.canonical_field_id,
                    "name": row.name,
                    "crop": row.crop,
                    "field_area_hectares": round((row.area_m2 or 0.0) / 10000.0, 2),
                    "index_name": "ndvi",
                    "field_value": round(row.mean, 4),
                    "farm_average": round(farm_mean, 4),
                    "status": status,
                    "anomaly_type": "/".join(anomaly_types),
                    "detail": "; ".join(details),
                }
            )

    return {
        "pass_date": latest_pass_date,
        "farm_average": round(farm_mean, 4),
        "anomalies": anomalies,
    }
