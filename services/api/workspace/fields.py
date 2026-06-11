"""Field read endpoints: a farm's fields with geometry for the map, per-field/index time
series, scene passes, the provenance audit log (invariant 5), and the on-demand collect
trigger."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from geoalchemy2.shape import to_shape
from pydantic import BaseModel
from rs_core.models import Analysis, Farm, Field
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.workspace.deps import RunAnalysisPrincipal, SessionDep, ViewPrincipal

router = APIRouter(tags=["workspace"])


class FieldOut(BaseModel):
    field_id: str
    canonical_field_id: str | None
    name: str | None
    crop: str | None
    geometry_version: int
    geometry: dict[str, Any]


class TimeseriesPoint(BaseModel):
    pass_date: date
    mean: float | None
    min: float | None
    max: float | None
    std: float | None
    p10: float | None
    p90: float | None
    clear_fraction: float
    confidence: str | None


class SceneOut(BaseModel):
    scene_id: str
    pass_date: date


class AuditRecordOut(BaseModel):
    """The provenance tuple (CLAUDE.md invariant 5) of one stored analysis, surfaced so an analyst
    can see exactly which scene, formula, geometry version, and processing mode produced a value.
    Read-only and geometry-free; this is internal reproducibility metadata, not the gateway push."""

    pass_date: date
    index_name: str
    scene_id: str
    provider: str
    provider_scene_id: str
    processing_mode: str
    formula_version: str
    geometry_version: int
    resolution_m: float
    clear_fraction: float
    confidence: str | None
    cog_uri: str | None
    created_at: datetime


async def list_fields(session: AsyncSession, canonical_farm_id: str) -> list[FieldOut]:
    fields = (
        (
            await session.execute(
                select(Field)
                .join(Farm, Field.farm_id == Farm.id)
                .where(Farm.canonical_farm_id == canonical_farm_id)
                .order_by(Field.canonical_field_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        FieldOut(
            field_id=str(f.id),
            canonical_field_id=f.canonical_field_id,
            name=f.name,
            crop=f.crop,
            geometry_version=f.geometry_version,
            geometry=mapping(to_shape(f.boundary)),
        )
        for f in fields
    ]


async def field_timeseries(
    session: AsyncSession, field_id: uuid.UUID, index: str
) -> list[TimeseriesPoint]:
    rows = (
        (
            await session.execute(
                select(Analysis)
                .where(Analysis.field_id == field_id, Analysis.index_name == index)
                .order_by(Analysis.pass_date)
            )
        )
        .scalars()
        .all()
    )
    return [
        TimeseriesPoint(
            pass_date=a.pass_date,
            mean=a.mean,
            min=a.min_val,
            max=a.max_val,
            std=a.std,
            p10=a.p10,
            p90=a.p90,
            clear_fraction=a.clear_fraction,
            confidence=a.confidence,
        )
        for a in rows
    ]


async def field_scenes(session: AsyncSession, field_id: uuid.UUID) -> list[SceneOut]:
    from sqlalchemy import func

    # Partition by pass_date to select the scene with the highest clear_fraction on that date
    subq = (
        select(
            Analysis.scene_id,
            Analysis.pass_date,
            func.row_number()
            .over(partition_by=Analysis.pass_date, order_by=Analysis.clear_fraction.desc())
            .label("rn"),
        )
        .where(Analysis.field_id == field_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(subq.c.scene_id, subq.c.pass_date)
            .where(subq.c.rn == 1)
            .order_by(subq.c.pass_date)
        )
    ).all()
    return [SceneOut(scene_id=scene_id, pass_date=pass_date) for scene_id, pass_date in rows]


async def field_audit(session: AsyncSession, field_id: uuid.UUID) -> list[AuditRecordOut]:
    """The provenance log for a field: every stored analysis with the tuple that reproduces it.
    Ordered newest-processed first, then newest pass, with index and scene as stable tiebreakers
    (rows from one run share created_at, so the extra keys keep the order deterministic)."""
    rows = (
        (
            await session.execute(
                select(Analysis)
                .where(Analysis.field_id == field_id)
                .order_by(
                    Analysis.created_at.desc(),
                    Analysis.pass_date.desc(),
                    Analysis.index_name,
                    Analysis.scene_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        AuditRecordOut(
            pass_date=a.pass_date,
            index_name=a.index_name,
            scene_id=a.scene_id,
            provider=a.provider,
            provider_scene_id=a.provider_scene_id,
            processing_mode=a.processing_mode,
            formula_version=a.formula_version,
            geometry_version=a.geometry_version,
            resolution_m=a.resolution_m,
            clear_fraction=a.clear_fraction,
            confidence=a.confidence,
            cog_uri=a.cog_uri,
            created_at=a.created_at,
        )
        for a in rows
    ]


@router.get("/farms/{canonical_farm_id}/fields")
async def list_fields_endpoint(
    canonical_farm_id: str, principal: ViewPrincipal, session: SessionDep
) -> list[FieldOut]:
    return await list_fields(session, canonical_farm_id)


@router.get("/fields/{field_id}/timeseries")
async def field_timeseries_endpoint(
    field_id: uuid.UUID, index: str, principal: ViewPrincipal, session: SessionDep
) -> list[TimeseriesPoint]:
    return await field_timeseries(session, field_id, index)


@router.get("/fields/{field_id}/scenes")
async def field_scenes_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[SceneOut]:
    return await field_scenes(session, field_id)


@router.get("/fields/{field_id}/audit")
async def field_audit_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[AuditRecordOut]:
    return await field_audit(session, field_id)


@router.post("/fields/{field_id}/collect", status_code=status.HTTP_202_ACCEPTED)
async def field_collect_endpoint(
    field_id: uuid.UUID,
    principal: RunAnalysisPrincipal,
    session: SessionDep,
) -> dict[str, str]:
    """Enqueue an on-demand backfill for one field so its history is collected now instead of
    waiting for the nightly scan. This only exposes the existing pipeline entrypoint:
    `backfill_field` is idempotent and gap-filling (it replans only outstanding passes), so repeated
    clicks are safe and converge the field to complete. A missing field is a 404, not a dangling
    task. Requires `run_analysis`."""
    from services.worker.tasks import backfill_field

    field_exists = (
        await session.execute(select(Field.id).where(Field.id == field_id))
    ).scalar_one_or_none()
    if field_exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    backfill_field.delay(str(field_id))
    return {"status": "enqueued", "field_id": str(field_id), "by": principal.subject}
