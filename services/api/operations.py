"""Operational API endpoints (Phase 7): the pipeline-health summary (R-4), RBAC-gated. The
publish trigger lives on the workspace surface (`POST /farms/{id}/publish`, workspace/publish.py);
the duplicate `POST /publish/farm/{id}` that used to live here was folded into it (S2.1
consolidation, 2026-06-11)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from rs_core import Permission, Principal, evaluate_health, get_logger, pipeline_health
from rs_core.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import require

router = APIRouter(tags=["operations"])
log = get_logger("operations")


@router.get("/pipeline/health")
async def pipeline_health_endpoint(
    principal: Annotated[Principal, Depends(require(Permission.VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """Pipeline coverage + failure summary for the health dashboard (R-4). Requires `view`. Fired
    alerts (dead-lettered pushes, backfill backlog) are logged so an operator or a log-based
    notifier can act on them; the summary itself is returned unchanged."""
    summary = await pipeline_health(session)
    for alert in evaluate_health(summary):
        log.warning(
            "pipeline.health.alert", level=alert.level, code=alert.code, message=alert.message
        )
    return summary
