"""Interpretation endpoints: per-field reads, the agronomist review action (the only path
that can publish a read, risk #6), and the cross-field review queue."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints
from rs_core import list_review_queue, review_interpretation
from rs_core.models import Interpretation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.workspace.deps import PublishPrincipal, SessionDep, ViewPrincipal

router = APIRouter(tags=["workspace"])


class InterpretationOut(BaseModel):
    id: str
    pass_date: date
    status: str
    confidence: str
    narrative: str
    published: bool
    needs_review: bool
    reviewed_by: str | None
    reviewed_at: datetime | None
    gdd_accumulation: float | None = None
    total_precipitation: float | None = None
    recent_activities: list[dict[str, Any]] | None = None


class InterpretationReview(BaseModel):
    """An agronomist's review action on a drafted read (risk #6): publish or withhold it, and
    optionally correct the narrative. `status`/`confidence` are grounded in the numbers and are not
    accepted here. `narrative=None` keeps the existing text; an empty string is rejected."""

    publish: bool
    narrative: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
        | None
    ) = None


class ReviewQueueItem(BaseModel):
    """One row of the cross-field review surface: a drafted/published read plus the canonical ids
    needed to open its field. Geometry-free (this is a triage list, not a map view)."""

    id: str
    field_id: str
    canonical_field_id: str | None
    canonical_farm_id: str
    field_name: str | None
    crop: str | None
    pass_date: date
    status: str
    confidence: str
    narrative: str
    published: bool
    needs_review: bool
    reviewed_by: str | None
    reviewed_at: datetime | None


async def field_interpretations(
    session: AsyncSession, field_id: uuid.UUID
) -> list[InterpretationOut]:
    rows = (
        (
            await session.execute(
                select(Interpretation)
                .where(Interpretation.field_id == field_id)
                .order_by(Interpretation.pass_date)
            )
        )
        .scalars()
        .all()
    )
    return [_interpretation_out(i) for i in rows]


def _interpretation_out(i: Interpretation) -> InterpretationOut:
    return InterpretationOut(
        id=str(i.id),
        pass_date=i.pass_date,
        status=i.status,
        confidence=i.confidence,
        narrative=i.narrative,
        published=i.published,
        needs_review=i.needs_review,
        reviewed_by=i.reviewed_by,
        reviewed_at=i.reviewed_at,
        gdd_accumulation=i.gdd_accumulation,
        total_precipitation=i.total_precipitation,
        recent_activities=i.recent_activities,
    )


async def review_queue(
    session: AsyncSession, *, needs_review: bool | None
) -> list[ReviewQueueItem]:
    """The cross-field agronomist review list, shaped from the geometry-free repo query."""
    rows = await list_review_queue(session, needs_review=needs_review)
    return [
        ReviewQueueItem(
            id=str(r.id),
            field_id=str(r.field_id),
            canonical_field_id=r.canonical_field_id,
            canonical_farm_id=r.canonical_farm_id,
            field_name=r.field_name,
            crop=r.crop,
            pass_date=r.pass_date,
            status=r.status,
            confidence=r.confidence,
            narrative=r.narrative,
            published=r.published,
            needs_review=r.needs_review,
            reviewed_by=r.reviewed_by,
            reviewed_at=r.reviewed_at,
        )
        for r in rows
    ]


@router.get("/fields/{field_id}/interpretations")
async def field_interpretations_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[InterpretationOut]:
    return await field_interpretations(session, field_id)


@router.patch("/fields/{field_id}/interpretations/{interpretation_id}")
async def review_interpretation_endpoint(
    field_id: uuid.UUID,
    interpretation_id: uuid.UUID,
    payload: InterpretationReview,
    principal: PublishPrincipal,
    session: SessionDep,
) -> InterpretationOut:
    """Review a drafted agronomic read: publish/withhold it and optionally correct its narrative.
    The only path that can publish one (risk #6, never auto-published). Gated on `publish`; the
    reviewer is the verified token subject, never client input. 404 if the read is not on this
    field. status/confidence stay immutable - they are grounded in the numbers, not the model."""
    row = await review_interpretation(
        session,
        interpretation_id=interpretation_id,
        field_id=field_id,
        reviewer=principal.subject,
        publish=payload.publish,
        narrative=payload.narrative,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interpretation not found")
    return _interpretation_out(row)


@router.get("/interpretations/review-queue")
async def review_queue_endpoint(
    principal: PublishPrincipal,
    session: SessionDep,
    needs_review: bool | None = None,
) -> list[ReviewQueueItem]:
    """The cross-field review backlog for an agronomist (gated on `publish`): every read with the
    canonical ids to open its field. `?needs_review=true` narrows to the unreviewed ones."""
    return await review_queue(session, needs_review=needs_review)
