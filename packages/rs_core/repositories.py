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

from sqlalchemy import Insert, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import (
    Analysis,
    Annotation,
    Farm,
    Field,
    FieldCollectionState,
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
