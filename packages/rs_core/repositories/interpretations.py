"""Drafted agronomic interpretations and their review lifecycle (risk #6): first-draft-wins
storage, the agronomist review action (the only path that can publish a read), and the
geometry-free read surfaces behind the gateway interpretation block and the review queue."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import Farm, Field, Interpretation


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
    gdd_accumulation: float | None = None,
    total_precipitation: float | None = None,
    recent_activities: list[dict] | None = None,
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
            gdd_accumulation=gdd_accumulation,
            total_precipitation=total_precipitation,
            recent_activities=recent_activities,
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
