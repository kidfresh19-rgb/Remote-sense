"""Ad-hoc AOI analysis endpoints: index stats for a drawn geometry, computed on the worker
through the production engine and never persisted (invariant 6 governs only the outbound push).

Two shapes: a single most-recent pass (`POST /analyse/aoi`, waited on inline) and a multi-pass
series (`POST /analyse/aoi/series` + `GET /analyse/aoi/jobs/{id}`, AOI Studio) - a batch of
specific dates, or a months-back backfill sweep. The series can run for minutes, so it is
enqueued and polled by job id rather than held open on the request."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from rs_core import get_settings

from services.api.workspace.deps import RunAnalysisPrincipal

router = APIRouter(tags=["workspace"])

# A batch is an analyst typing in a handful of dates, not a bulk import - cap it so one request
# can never fan out to an unbounded number of reads. The backfill cap lives on the worker.
MAX_BATCH_DATES = 24


class AOIAnalysisRequest(BaseModel):
    geometry: dict[str, Any]
    index: str


class AOISeriesRequest(BaseModel):
    """A multi-pass AOI preview. `mode="dates"` resolves each requested calendar date to its
    same-day scene (exact day only); `mode="backfill"` sweeps `months` of history (default + max
    is the configured backfill depth)."""

    geometry: dict[str, Any]
    index: str
    mode: Literal["dates", "backfill"]
    dates: list[date] | None = None
    months: int | None = None


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


@router.post("/analyse/aoi/series", status_code=status.HTTP_202_ACCEPTED)
async def analyse_aoi_series_endpoint(
    payload: AOISeriesRequest,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Start a multi-pass AOI preview (AOI Studio) and return its job id immediately. The sweep can
    take minutes, so it is not waited on here - poll `GET /analyse/aoi/jobs/{job_id}` for progress
    and results. Nothing is persisted (invariant 6). Requires `run_analysis`. A bad index, an empty
    or oversized date batch, or out-of-range months are rejected as 422 before anything is queued.
    """
    from rs_analysis import get_index

    from services.worker.tasks import analyse_aoi_series_task

    try:
        get_index(payload.index)
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    settings = get_settings()
    if payload.mode == "dates":
        dates = payload.dates or []
        if not 1 <= len(dates) <= MAX_BATCH_DATES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"provide between 1 and {MAX_BATCH_DATES} dates",
            )
        today = datetime.now(UTC).date()
        if any(d > today for d in dates):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "dates cannot be in the future"
            )
        async_result = analyse_aoi_series_task.delay(
            payload.geometry, payload.index, "dates", [d.isoformat() for d in dates], None
        )
    else:  # backfill
        months = payload.months or settings.backfill_months
        if not 1 <= months <= settings.backfill_months:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"months must be between 1 and {settings.backfill_months}",
            )
        async_result = analyse_aoi_series_task.delay(
            payload.geometry, payload.index, "backfill", None, months
        )

    return {"job_id": async_result.id, "state": "queued"}


def _job_status(job_id: str) -> dict[str, Any]:
    """Map a Celery `AsyncResult` to the workspace's small job shape. Runs in a threadpool because
    each attribute read hits the redis result backend. An unknown/expired id reads as PENDING,
    which we report as `queued` (redis cannot distinguish it from a job still waiting to start)."""
    from celery.result import AsyncResult

    from services.worker.celery_app import celery

    result = AsyncResult(job_id, app=celery)
    state = result.state
    if state == "SUCCESS":
        return {"job_id": job_id, "state": "done", "result": result.result}
    if state == "FAILURE":
        return {"job_id": job_id, "state": "error", "error": str(result.result)}
    if state in ("STARTED", "PROGRESS"):
        info = result.info if isinstance(result.info, dict) else {}
        return {
            "job_id": job_id,
            "state": "running",
            "progress": {"done": info.get("done"), "total": info.get("total")},
        }
    return {"job_id": job_id, "state": "queued"}


@router.get("/analyse/aoi/jobs/{job_id}")
async def aoi_job_endpoint(
    job_id: str,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Status + results for a multi-pass AOI preview job. `state` is `queued` | `running` (with a
    `{done, total}` progress meter) | `done` (with `result`) | `error` (with `error`). Requires
    `run_analysis`."""
    return await run_in_threadpool(_job_status, job_id)
