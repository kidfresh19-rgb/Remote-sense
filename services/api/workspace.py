"""Analyst workspace BFF (L6): RBAC-gated (view) read endpoints backing the React workspace -
farms, fields (with geometry for the map), per-field/index time series, scene passes, and
interpretations. Read-only. Geometry IS shown to the internal analyst here, which is distinct from
the geometry-free *outbound* push (invariant 6 governs the gateway direction only)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from geoalchemy2.shape import to_shape
from pydantic import BaseModel
from rs_core import Permission, Principal
from rs_core.db import get_session
from rs_core.models import Analysis, Farm, Field, Interpretation
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import require

router = APIRouter(tags=["workspace"])

ViewPrincipal = Annotated[Principal, Depends(require(Permission.VIEW))]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class FarmOut(BaseModel):
    canonical_farm_id: str
    name: str | None
    region: str | None


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


class InterpretationOut(BaseModel):
    pass_date: date
    status: str
    confidence: str
    narrative: str
    published: bool
    needs_review: bool


async def list_farms(session: AsyncSession) -> list[FarmOut]:
    farms = (await session.execute(select(Farm).order_by(Farm.canonical_farm_id))).scalars().all()
    return [
        FarmOut(canonical_farm_id=f.canonical_farm_id, name=f.name, region=f.region) for f in farms
    ]


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
    rows = (
        await session.execute(
            select(Analysis.scene_id, Analysis.pass_date)
            .where(Analysis.field_id == field_id)
            .distinct()
            .order_by(Analysis.pass_date)
        )
    ).all()
    return [SceneOut(scene_id=scene_id, pass_date=pass_date) for scene_id, pass_date in rows]


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
    return [
        InterpretationOut(
            pass_date=i.pass_date,
            status=i.status,
            confidence=i.confidence,
            narrative=i.narrative,
            published=i.published,
            needs_review=i.needs_review,
        )
        for i in rows
    ]


@router.get("/farms")
async def list_farms_endpoint(principal: ViewPrincipal, session: SessionDep) -> list[FarmOut]:
    return await list_farms(session)


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


@router.get("/fields/{field_id}/interpretations")
async def field_interpretations_endpoint(
    field_id: uuid.UUID, principal: ViewPrincipal, session: SessionDep
) -> list[InterpretationOut]:
    return await field_interpretations(session, field_id)
