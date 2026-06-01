"""Operational API endpoints (Phase 7): the publish trigger (L7) and the pipeline-health summary
(R-4), both RBAC-gated. The same auth fronts the future workspace BFF (L6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from rs_core import Permission, Principal, pipeline_health
from rs_core.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import require

router = APIRouter(tags=["operations"])


@router.post("/publish/farm/{canonical_farm_id}", status_code=status.HTTP_202_ACCEPTED)
async def publish_farm_endpoint(
    canonical_farm_id: str,
    principal: Annotated[Principal, Depends(require(Permission.PUBLISH))],
) -> dict[str, str]:
    """Enqueue an additive push of a farm's results to the gateway (L7). Requires `publish`."""
    from services.worker.tasks import publish_farm_task

    publish_farm_task.delay(canonical_farm_id)
    return {"status": "enqueued", "canonical_farm_id": canonical_farm_id, "by": principal.subject}


@router.get("/pipeline/health")
async def pipeline_health_endpoint(
    principal: Annotated[Principal, Depends(require(Permission.VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """Pipeline coverage + failure summary for the health dashboard (R-4). Requires `view`."""
    return await pipeline_health(session)
