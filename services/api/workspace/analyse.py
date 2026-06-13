"""Ad-hoc AOI analysis endpoint: index stats for a drawn geometry, computed on the worker
through the production engine and never persisted (invariant 6 governs only the outbound
push)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from services.api.workspace.deps import RunAnalysisPrincipal

router = APIRouter(tags=["workspace"])


class AOIAnalysisRequest(BaseModel):
    geometry: dict[str, Any]
    index: str


@router.post("/analyse/aoi")
async def analyse_aoi_endpoint(
    payload: AOIAnalysisRequest,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Ad-hoc preview analysis over a custom AOI: returns the most recent usable pass's index stats
    for the drawn geometry, computed on the worker (geo extra) through the production engine.
    Nothing is persisted - no field is created, so it cannot collide with gateway-owned identity
    (invariant 6 governs only the outbound push). Requires `run_analysis`.

    The work runs on the worker (it needs rasterio + CDSE), so we enqueue and wait for the result
    off the event loop; a bad index name is rejected up front as a 422 rather than a worker failure.
    """
    from celery.exceptions import TimeoutError as CeleryTimeoutError
    from rs_analysis import get_index

    from services.worker.tasks import analyse_aoi_task

    try:
        get_index(payload.index)
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    async_result = analyse_aoi_task.delay(payload.geometry, payload.index)
    try:
        return await run_in_threadpool(async_result.get, timeout=75)
    except CeleryTimeoutError as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "AOI analysis timed out; the imagery service is slow right now. Try again.",
        ) from exc
    except Exception as exc:  # the worker task raised (e.g. no imagery / fetch error)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AOI analysis failed: {exc}") from exc
