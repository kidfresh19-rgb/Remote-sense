"""Officer field-diagnosis persistence and the labelled-set read (backlog 0038).

Records one diagnosis per plot as a controlled-vocab ground-truth point (the flywheel, PRD 0003 §0)
and reads diagnoses back as a labelled set for export or for a household's visit history. Validation
lives in the pure `rs_core.diagnosis` layer (the API calls it and maps a ValueError to 422); this
module takes the already-validated `DiagnosisFields` and persists them with provenance, so the store
never holds an unvalidated label. Does not commit - the caller owns the transaction."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.diagnosis import DiagnosisFields
from rs_core.models import Diagnosis, Household, Plot


async def record_diagnosis(
    session: AsyncSession,
    *,
    plot_id: uuid.UUID,
    fields: DiagnosisFields,
    observed_on: date,
    scene_id: str | None = None,
    officer_id: str | None = None,
) -> Diagnosis:
    """Persist one validated field diagnosis for a plot and return the stored row. `fields` is the
    controlled-vocab core (already validated, `rs_core.diagnosis`); `scene_id` is the provenance
    pass it was observed against (invariant 5); `officer_id` is the verified token subject, never
    client input. Flushes but does not commit."""
    diagnosis = Diagnosis(
        plot_id=plot_id,
        scene_id=scene_id,
        observed_on=observed_on,
        observed_crop=fields.observed_crop,
        condition=fields.condition.value,
        cause=fields.cause.value,
        recommended_action=(
            fields.recommended_action.value if fields.recommended_action is not None else None
        ),
        notes=fields.notes,
        officer_id=officer_id,
    )
    session.add(diagnosis)
    await session.flush()
    return diagnosis


async def list_diagnoses(
    session: AsyncSession, *, ward: str | None = None, limit: int = 500
) -> Sequence[Diagnosis]:
    """The recorded diagnoses as a labelled set for export, newest observation first. `ward` filters
    to one ward (joined through the plot's household); `limit` caps the page."""
    stmt = select(Diagnosis)
    if ward is not None:
        stmt = (
            stmt.join(Plot, Plot.id == Diagnosis.plot_id)
            .join(Household, Household.id == Plot.household_id)
            .where(func.lower(Household.ward_name) == ward.strip().lower())
        )
    stmt = stmt.order_by(Diagnosis.observed_on.desc(), Diagnosis.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def diagnoses_for_household(
    session: AsyncSession, household_id: uuid.UUID
) -> Sequence[Diagnosis]:
    """Every diagnosis recorded against a household's plots, newest observation first - the visit
    history the visit package surfaces (0037 `previous_visits`)."""
    stmt = (
        select(Diagnosis)
        .join(Plot, Plot.id == Diagnosis.plot_id)
        .where(Plot.household_id == household_id)
        .order_by(Diagnosis.observed_on.desc(), Diagnosis.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()
