"""Farm publish endpoints: enqueue a gateway push for one farm and poll its delivery status.
The S2.1 triage flags this pair for consolidation with operations.py POST /publish/farm/{id};
keeping it isolated here makes that follow-up surgical."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from rs_core import get_latest_outbox_for_farm, get_settings
from rs_core.models import Farm
from sqlalchemy import select

from services.api.workspace.deps import PublishPrincipal, SessionDep
from services.worker.publish import gateway_config_error, gateway_is_dry_run

router = APIRouter(tags=["workspace"])


class PublishEnqueuedOut(BaseModel):
    status: str
    canonical_farm_id: str
    by: str
    gateway: str  # active adapter: recording | http | agritrack
    dry_run: bool  # True when the active gateway records without sending (recording sink)


@router.post("/farms/{canonical_farm_id}/publish", status_code=status.HTTP_202_ACCEPTED)
async def publish_farm_endpoint(
    canonical_farm_id: str,
    principal: PublishPrincipal,
    session: SessionDep,
) -> PublishEnqueuedOut:
    """Enqueue a gateway push for one farm so its results are sent now instead of waiting for the
    next scheduled sync. Reuses the existing ``publish_farm_task`` Celery path, which is idempotent
    (R-2). Requires ``publish``.

    A gateway configured for real delivery (``agritrack``/``http``) but missing its URL or key fails
    fast with 503 here, so the operator sees the misconfiguration immediately rather than enqueuing
    a task that cannot deliver and leaves the workspace polling forever."""
    from services.worker.tasks import publish_farm_task

    farm_exists = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if farm_exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "farm not found")
    settings = get_settings()
    config_error = gateway_config_error(settings)
    if config_error is not None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, config_error)
    publish_farm_task.delay(canonical_farm_id)
    return PublishEnqueuedOut(
        status="enqueued",
        canonical_farm_id=canonical_farm_id,
        by=principal.subject,
        gateway=settings.gateway_adapter.value,
        dry_run=gateway_is_dry_run(settings),
    )


class PublishStatusOut(BaseModel):
    canonical_farm_id: str
    status: str  # pending | published | dead_letter
    result_count: int
    pushed_at: datetime | None
    last_error: str | None
    gateway: str  # active adapter: recording | http | agritrack
    dry_run: bool  # True when the active gateway records without sending (recording sink)


@router.get("/farms/{canonical_farm_id}/publish/status")
async def publish_status_endpoint(
    canonical_farm_id: str,
    principal: PublishPrincipal,
    session: SessionDep,
) -> PublishStatusOut:
    """The latest gateway push outcome for a farm. The frontend polls this after enqueuing a push
    to confirm delivery (published), surface errors (dead_letter), or show in-progress (pending).
    Returns 404 if no push has ever been recorded for this farm."""
    row = await get_latest_outbox_for_farm(session, canonical_farm_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no push recorded for this farm")
    settings = get_settings()
    return PublishStatusOut(
        canonical_farm_id=canonical_farm_id,
        status=row.status,
        result_count=row.result_count,
        pushed_at=row.pushed_at,
        last_error=row.last_error,
        gateway=settings.gateway_adapter.value,
        dry_run=gateway_is_dry_run(settings),
    )
