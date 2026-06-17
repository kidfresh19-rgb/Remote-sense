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
from rs_core.logging import get_logger

from services.api.workspace.deps import RunAnalysisPrincipal
from services.worker.publish import gateway_from_settings, gateway_is_dry_run

log = get_logger("services.api.workspace.analyse")

router = APIRouter(tags=["workspace"])

# A batch is an analyst typing in a handful of dates, not a bulk import - cap it so one request
# can never fan out to an unbounded number of reads. The backfill cap lives on the worker.
MAX_BATCH_DATES = 24


class AOIAnalysisRequest(BaseModel):
    geometry: dict[str, Any]
    index: str


class AOIPushRequest(BaseModel):
    """Push passes of a completed AOI Studio job to the gateway, tagged to a farm.
    Both exact same-day passes (status='ok') and averaged/interpolated passes
    (status='interpolated') are included."""

    canonical_farm_id: str


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

    log.info("aoi.series.dispatch", index=payload.index, mode=payload.mode)
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


class FarmSeriesRequest(BaseModel):
    """Multi-pass AOI preview over an entire farm: the farm's stored field geometries are unioned
    server-side so the analyst doesn't need to draw or upload a boundary. Same mode/date/months
    semantics as `AOISeriesRequest`, but the geometry comes from the DB."""

    index: str
    mode: Literal["dates", "backfill"]
    dates: list[date] | None = None
    months: int | None = None


@router.post("/analyse/farm/{canonical_farm_id}/series", status_code=status.HTTP_202_ACCEPTED)
async def analyse_farm_series_endpoint(
    canonical_farm_id: str,
    payload: FarmSeriesRequest,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Start a multi-pass AOI preview (AOI Studio) for an entire farm: the farm's stored field
    geometries are unioned into a single AOI on the worker, then the same engine as
    `analyse_aoi_series_endpoint` runs. Returns a job id immediately; poll
    `GET /analyse/aoi/jobs/{job_id}` for progress and results. Nothing is persisted (invariant 6).
    Requires `run_analysis`. A bad index, an empty date batch, or out-of-range months are rejected
    as 422 before anything is queued. A farm with no stored field geometries surfaces as a worker
    failure (the task raises ValueError, which Celery stores as FAILURE)."""
    from rs_analysis import get_index

    from services.worker.tasks import analyse_farm_series_task

    try:
        get_index(payload.index)
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    log.info("aoi.series.dispatch", index=payload.index, mode=payload.mode)
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
        async_result = analyse_farm_series_task.delay(
            canonical_farm_id, payload.index, "dates", [d.isoformat() for d in dates], None
        )
    else:  # backfill
        months = payload.months or settings.backfill_months
        if not 1 <= months <= settings.backfill_months:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"months must be between 1 and {settings.backfill_months}",
            )
        async_result = analyse_farm_series_task.delay(
            canonical_farm_id, payload.index, "backfill", None, months
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


@router.post("/analyse/aoi/jobs/{job_id}/push")
async def push_aoi_results_endpoint(
    job_id: str,
    payload: AOIPushRequest,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Push passes of a completed AOI Studio job to the gateway under a farm's canonical id.
    Both exact same-day passes (status='ok') and averaged/interpolated passes
    (status='interpolated') are included. For interpolated passes, a synthetic
    provider_scene_id is built from the two source scenes and processing_mode is set to
    'interpolated' so the gateway can distinguish them. Requires `run_analysis`.

    # ⚑ CONFIRM: deliberate relaxation of invariant 6 for AOI Studio - ad-hoc analyses are not
    persisted to the DB but can be pushed to the gateway tagged to an existing farm."""
    from celery.result import AsyncResult
    from rs_sync.payload import IndexResult, build_payload

    from services.worker.celery_app import celery

    def _get_result() -> dict[str, Any] | None:
        r = AsyncResult(job_id, app=celery)
        return r.result if r.state == "SUCCESS" else None  # type: ignore[return-value]

    job_result = await run_in_threadpool(_get_result)
    if job_result is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "job is not done yet or was not found; only completed jobs can be pushed",
        )

    pushable_passes = [
        p
        for p in job_result.get("passes", [])
        if p.get("status") in ("ok", "interpolated")
    ]
    if not pushable_passes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "no passes to push; run an analysis first and ensure at least one pass resolved",
        )

    def _build_result(p: dict[str, Any]) -> IndexResult:
        """Map a single pass dict (ok or interpolated) onto an IndexResult."""
        is_interpolated = p.get("status") == "interpolated"

        if is_interpolated:
            # Interpolated passes are averaged from two source scenes.  Use the earlier
            # source date as the canonical pass_date so the gateway record is anchored to a
            # real observation window.
            before: dict[str, Any] = p.get("before") or {}
            after: dict[str, Any] = p.get("after") or {}
            pass_date_str = (
                p.get("before_pass_date")
                or before.get("pass_date")
                or p.get("requested_date")
                or p.get("pass_date")
            )
            before_sid = before.get("provider_scene_id") or before.get("scene_id", "")
            after_sid = after.get("provider_scene_id") or after.get("scene_id", "")
            scene_id = (
                f"avg:{before_sid}+{after_sid}" if (before_sid or after_sid) else "interpolated"
            )
            provider = before.get("provider") or after.get("provider") or "interpolated"
            # Average clear_fraction from the two sub-passes when available.
            cf_before = before.get("clear_fraction")
            cf_after = after.get("clear_fraction")
            if cf_before is not None and cf_after is not None:
                clear_fraction = (cf_before + cf_after) / 2.0
            else:
                clear_fraction = p.get("clear_fraction", 0.0)
            processing_mode = "interpolated"
        else:
            pass_date_str = p.get("pass_date")
            scene_id = p.get("provider_scene_id") or p.get("scene_id", "unknown")
            provider = p.get("provider", "unknown")
            clear_fraction = p.get("clear_fraction", 0.0)
            processing_mode = p.get("processing_mode", "unknown")

        if not pass_date_str:
            raise ValueError(f"pass is missing a date: {p!r}")

        return IndexResult(
            canonical_field_id=None,
            index_name=p["index"],
            pass_date=date.fromisoformat(pass_date_str),
            mean=p.get("mean"),
            min=p.get("min"),
            max=p.get("max"),
            std=None,
            p10=p.get("p10"),
            p90=p.get("p90"),
            clear_fraction=clear_fraction,
            confidence=p.get("confidence"),
            resolution_m=float(p.get("resolution_m", 10)),
            formula_version=p.get("formula_version", "unknown"),
            provider=provider,
            provider_scene_id=scene_id,
            processing_mode=processing_mode,
        )

    results = [_build_result(p) for p in pushable_passes]

    settings = get_settings()
    gateway = gateway_from_settings(settings)
    gw_payload = build_payload(
        payload.canonical_farm_id,
        results,
        generated_at=datetime.now(UTC),
        destination=gateway.destination_key(),
    )
    outcome = await gateway.push(gw_payload)
    return {
        "pushed_passes": len(results),
        "status": outcome.status,
        "ok": outcome.ok,
        "dry_run": gateway_is_dry_run(settings),
        "idempotency_key": gw_payload.idempotency_key,
        "detail": outcome.detail,
    }
