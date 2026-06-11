"""Farm read endpoints: the farm list with its analytics summary (health, latest pass, area,
crops)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel
from rs_core import get_farm_analytics_summary
from rs_core.models import Farm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.workspace.deps import SessionDep, ViewPrincipal

router = APIRouter(tags=["workspace"])


class FarmOut(BaseModel):
    canonical_farm_id: str
    name: str | None
    region: str | None
    overall_health: str | None = None
    overall_health_score: float | None = None
    latest_pass_date: date | None = None
    total_fields: int | None = None
    total_area_hectares: float | None = None
    crops: list[str] | None = None


async def list_farms(session: AsyncSession) -> list[FarmOut]:
    farms = (await session.execute(select(Farm).order_by(Farm.canonical_farm_id))).scalars().all()
    out = []
    for f in farms:
        summary = await get_farm_analytics_summary(session, f.canonical_farm_id)
        if summary:
            out.append(
                FarmOut(
                    canonical_farm_id=f.canonical_farm_id,
                    name=f.name,
                    region=f.region,
                    overall_health=summary["overall_health"],
                    overall_health_score=summary["overall_health_score"],
                    latest_pass_date=summary["latest_pass_date"],
                    total_fields=summary["total_fields"],
                    total_area_hectares=summary["total_area_hectares"],
                    crops=summary["crops"],
                )
            )
        else:
            out.append(FarmOut(canonical_farm_id=f.canonical_farm_id, name=f.name, region=f.region))
    return out


@router.get("/farms")
async def list_farms_endpoint(principal: ViewPrincipal, session: SessionDep) -> list[FarmOut]:
    return await list_farms(session)
