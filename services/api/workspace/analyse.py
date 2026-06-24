"""Ad-hoc AOI analysis endpoints: index stats for a drawn geometry, computed on the worker
through the production engine and never persisted (invariant 6 governs only the outbound push).

Two shapes: a single most-recent pass (`POST /analyse/aoi`, waited on inline) and a multi-pass
series (`POST /analyse/aoi/series` + `GET /analyse/aoi/jobs/{id}`, AOI Studio) - a batch of
specific dates, or a months-back backfill sweep. The series can run for minutes, so it is
enqueued and polled by job id rather than held open on the request."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, model_validator
from rs_core import Settings, get_settings
from rs_core.logging import get_logger

from services.api.workspace.deps import RunAnalysisPrincipal
from services.worker.publish import gateway_from_settings, gateway_is_dry_run

log = get_logger("services.api.workspace.analyse")

router = APIRouter(tags=["workspace"])

# A batch is an analyst typing in a handful of dates, not a bulk import - cap it so one request
# can never fan out to an unbounded number of reads. The backfill cap lives on the worker.
MAX_BATCH_DATES = 24

# The all-passes orthophoto bundle renders one RGB COG per usable pass into an in-memory zip;
# cap the pass count (~ a year of Sentinel-2 same-day passes) to bound the worker's memory.
MAX_BUNDLE_PASSES = 60


def _require_exactly_one_index(index: str | None, indices: list[str] | None) -> None:
    """Series requests carry either a single `index` (single-index path) or a non-empty `indices`
    list (the all-indices path, ADR 0011 Phase 2), never both and never neither. Raised as a
    Pydantic ValueError so FastAPI surfaces it as a 422."""
    if (index is None) == (indices is None):
        raise ValueError("provide exactly one of `index` or `indices`")
    if indices is not None and not indices:
        raise ValueError("`indices` must be non-empty")


def _validate_index_names(names: list[str]) -> None:
    """422 on the first unknown index name, before anything is queued."""
    from rs_analysis import get_index

    for name in names:
        try:
            get_index(name)
        except KeyError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _series_window(
    mode: str, dates: list[date] | None, months: int | None, settings: Settings
) -> tuple[list[str] | None, int | None]:
    """Validate and normalise the series window once for both the single- and all-indices paths,
    returning the `(iso_dates, months)` pair the worker tasks take: dates mode yields the ISO date
    list and a None months; backfill yields None dates and the clamped months. 422 on an empty or
    oversized batch, a future date, or out-of-range months."""
    if mode == "dates":
        batch = dates or []
        if not 1 <= len(batch) <= MAX_BATCH_DATES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"provide between 1 and {MAX_BATCH_DATES} dates",
            )
        today = datetime.now(UTC).date()
        if any(d > today for d in batch):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "dates cannot be in the future"
            )
        return [d.isoformat() for d in batch], None
    resolved_months = months or settings.backfill_months
    if not 1 <= resolved_months <= settings.backfill_months:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"months must be between 1 and {settings.backfill_months}",
        )
    return None, resolved_months


class AOIAnalysisRequest(BaseModel):
    geometry: dict[str, Any]
    index: str


class NaturalColorRequest(BaseModel):
    scene_id: str
    geometry: dict[str, Any]
    pass_date: str
    # Which artifact to return: the 512px true-colour JPEG (default) or the georeferenced RGB
    # reflectance GeoTIFF for download. Both are rendered and cached from one band read.
    format: Literal["jpeg", "cog"] = "jpeg"


class AOIPushRequest(BaseModel):
    """Push passes of a completed AOI Studio job to the gateway, tagged to a farm.
    Both exact same-day passes (status='ok') and averaged/interpolated passes
    (status='interpolated') are included."""

    canonical_farm_id: str


class OrthophotoBundleRequest(BaseModel):
    """The drawn AOI geometry for an all-passes orthophoto bundle. The passes are read server-side
    from the completed series job, so only the geometry (which a preview never persists) is sent."""

    geometry: dict[str, Any]


class AOISeriesRequest(BaseModel):
    """A multi-pass AOI preview. `mode="dates"` resolves each requested calendar date to its
    same-day scene (exact day only); `mode="backfill"` sweeps `months` of history (default + max
    is the configured backfill depth). Provide either a single `index` or a non-empty `indices`
    list for the all-indices path (ADR 0011 Phase 2), never both."""

    geometry: dict[str, Any]
    index: str | None = None
    indices: list[str] | None = None
    mode: Literal["dates", "backfill"]
    dates: list[date] | None = None
    months: int | None = None

    @model_validator(mode="after")
    def _one_index_field(self) -> AOISeriesRequest:
        _require_exactly_one_index(self.index, self.indices)
        return self


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


@router.post("/analyse/aoi/natural-color")
async def aoi_natural_color_endpoint(
    payload: NaturalColorRequest,
    principal: RunAnalysisPrincipal,
) -> Response:
    """Render the natural-colour artifacts for one custom AOI scene and return the requested
    `format`: the 512px true-colour JPEG (default) or the georeferenced RGB reflectance GeoTIFF
    (`format="cog"`, served as a download attachment). On cache hit the stored object is proxied
    from MinIO. On cache miss the render runs on the worker (CDSE B02/B03/B04 fetch + in-memory
    COG) and both artifacts are cached under the `aoi_preview/` prefix (7-day lifecycle), so the
    companion format is a cache hit afterwards. Requires `run_analysis`."""
    import base64

    from celery.exceptions import TimeoutError as CeleryTimeoutError
    from rs_core.geo import canonical_geometry_hash
    from rs_core.storage import S3CogStore, aoi_preview_key, aoi_rgb_cog_key

    from services.worker.tasks import render_natural_color_task

    settings = get_settings()
    geom_hash = canonical_geometry_hash(payload.geometry)
    jpeg_key = aoi_preview_key(payload.scene_id, geom_hash)
    cog_key = aoi_rgb_cog_key(payload.scene_id, geom_hash)
    want_cog = payload.format == "cog"
    target_key = cog_key if want_cog else jpeg_key
    media_type = "image/tiff" if want_cog else "image/jpeg"
    headers = (
        {
            "Content-Disposition": (
                f'attachment; filename="rgb_{payload.scene_id}_{payload.pass_date}.tif"'
            )
        }
        if want_cog
        else None
    )

    # Cache hit (either format): proxy the stored object from MinIO. Fast - no render.
    store: S3CogStore | None
    try:
        store = S3CogStore(settings)
    except Exception:
        store = None  # storage unavailable; fall through to rendering
    if store is not None and await run_in_threadpool(store.exists, target_key):
        data = await run_in_threadpool(store.get_bytes, target_key)
        return Response(content=data, media_type=media_type, headers=headers)

    # The GeoTIFF download is served from the cache only; without object storage the COG can never
    # be read back, so fail fast rather than enqueue a render whose output we could never serve.
    if want_cog and store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "object storage not available")

    # Cache miss: render on the worker, persisting BOTH the JPEG and the COG from one band read.
    task = render_natural_color_task.delay(
        payload.scene_id, payload.geometry, payload.pass_date, jpeg_key, cog_key
    )

    if want_cog:
        # A cold COG render can take ~90 s. Do not hold a Starlette threadpool thread waiting on it:
        # hand back the job id with 202, let the client poll GET /analyse/aoi/jobs/{id}, then
        # re-request once it is done - which is then the cache hit served above.
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"job_id": task.id, "state": "queued"},
        )

    # JPEG path (lazy filmstrip thumbnails): the inline wait is fine and keeps the hook simple.
    try:
        b64_result: str = await run_in_threadpool(task.get, timeout=90)
    except CeleryTimeoutError as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "Natural-colour render timed out. Try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Natural-colour render failed: {exc}"
        ) from exc
    return Response(content=base64.b64decode(b64_result), media_type="image/jpeg")


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
    from services.worker.tasks import analyse_aoi_series_multi_task, analyse_aoi_series_task

    names = payload.indices if payload.indices is not None else [payload.index]
    assert all(n is not None for n in names)  # the validator guarantees one path or the other
    _validate_index_names([n for n in names if n is not None])

    settings = get_settings()
    iso_dates, months = _series_window(payload.mode, payload.dates, payload.months, settings)

    if payload.indices is not None:
        log.info("aoi.series.dispatch", indices=payload.indices, mode=payload.mode)
        async_result = analyse_aoi_series_multi_task.delay(
            payload.geometry, payload.indices, payload.mode, iso_dates, months
        )
    else:
        log.info("aoi.series.dispatch", index=payload.index, mode=payload.mode)
        async_result = analyse_aoi_series_task.delay(
            payload.geometry, payload.index, payload.mode, iso_dates, months
        )

    return {"job_id": async_result.id, "state": "queued"}


class FarmSeriesRequest(BaseModel):
    """Multi-pass AOI preview over an entire farm: the farm's stored field geometries are unioned
    server-side so the analyst doesn't need to draw or upload a boundary. Same mode/date/months and
    single-`index`-or-`indices` semantics as `AOISeriesRequest`, but the geometry comes from the
    DB."""

    index: str | None = None
    indices: list[str] | None = None
    mode: Literal["dates", "backfill"]
    dates: list[date] | None = None
    months: int | None = None

    @model_validator(mode="after")
    def _one_index_field(self) -> FarmSeriesRequest:
        _require_exactly_one_index(self.index, self.indices)
        return self


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
    from services.worker.tasks import analyse_farm_series_multi_task, analyse_farm_series_task

    names = payload.indices if payload.indices is not None else [payload.index]
    _validate_index_names([n for n in names if n is not None])

    settings = get_settings()
    iso_dates, months = _series_window(payload.mode, payload.dates, payload.months, settings)

    if payload.indices is not None:
        log.info("aoi.series.dispatch", indices=payload.indices, mode=payload.mode)
        async_result = analyse_farm_series_multi_task.delay(
            canonical_farm_id, payload.indices, payload.mode, iso_dates, months
        )
    else:
        log.info("aoi.series.dispatch", index=payload.index, mode=payload.mode)
        async_result = analyse_farm_series_task.delay(
            canonical_farm_id, payload.index, payload.mode, iso_dates, months
        )

    return {"job_id": async_result.id, "state": "queued"}


@router.get("/analyse/aoi/jobs/{job_id}/passes/{pass_date}/download")
async def aoi_job_pass_download_endpoint(
    job_id: str,
    pass_date: str,
    principal: RunAnalysisPrincipal,
    index: str = "ndvi",
) -> Response:
    """Proxy an index GeoTIFF download for one AOI Studio job pass. Returns 404 when the temp COG
    was not emitted (cache hit at analysis time, interpolated pass, or 24-hour TTL expired).
    Requires `run_analysis`."""
    from rs_core.storage import S3CogStore, aoi_tmp_cog_key

    settings = get_settings()
    try:
        store = S3CogStore(settings)
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "object storage not available"
        ) from exc

    key = aoi_tmp_cog_key(job_id, pass_date, index)
    if not await run_in_threadpool(store.exists, key):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "COG not available or expired for this pass",
        )

    filename = f"{index}_{pass_date}.tif"
    url = await run_in_threadpool(store.presigned_url, key, filename=filename)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


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


def _all_passes(job_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Every pass of a completed series job, across both result shapes: the single-index job has a
    top-level `passes` list; the all-indices job (ADR 0011 Phase 2) nests one series result per
    index under `indices`, so we flatten across them. Each pass carries its own `index`, so the
    per-pass mapping in `_build_result` is identical for either shape."""
    indices = job_result.get("indices")
    if isinstance(indices, dict):
        passes: list[dict[str, Any]] = []
        for series in indices.values():
            if isinstance(series, dict):
                passes.extend(series.get("passes", []))
        return passes
    return list(job_result.get("passes", []))


@router.post("/analyse/aoi/jobs/{job_id}/orthophoto-bundle", status_code=status.HTTP_202_ACCEPTED)
async def start_orthophoto_bundle_endpoint(
    job_id: str,
    payload: OrthophotoBundleRequest,
    principal: RunAnalysisPrincipal,
) -> dict[str, Any]:
    """Bundle every usable pass of a completed AOI Studio job into a zip of georeferenced RGB
    orthophoto GeoTIFFs - the all-passes companion to the single-pass download. The ok passes
    are read from the job result, then a bulk render+zip task is enqueued (off the interactive lane)
    and its id returned immediately: poll `GET /analyse/aoi/jobs/{bundle_job_id}` for progress, then
    `GET /analyse/aoi/orthophoto-bundle/{bundle_job_id}/download` once done. Nothing is persisted
    (invariant 6). Requires `run_analysis`. 409 if the job is not done; 422 if it has no usable pass
    or more than the bundle cap."""
    import uuid

    from celery.result import AsyncResult
    from rs_core.storage import aoi_bundle_zip_key

    from services.worker.celery_app import celery
    from services.worker.tasks import bundle_aoi_orthophotos_task

    def _get_result() -> dict[str, Any] | None:
        r = AsyncResult(job_id, app=celery)
        return r.result if r.state == "SUCCESS" else None  # type: ignore[return-value]

    job_result = await run_in_threadpool(_get_result)
    if job_result is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "job is not done yet or was not found; only completed jobs can be bundled",
        )

    # One render per (scene_id, pass_date): an all-indices job repeats each scene once per index, so
    # dedupe to avoid rendering and zipping the same orthophoto several times.
    seen: set[tuple[str, str]] = set()
    passes: list[list[str]] = []
    for p in _all_passes(job_result):
        if p.get("status") != "ok":
            continue
        scene_id = p.get("scene_id")
        pass_date = p.get("pass_date")
        if not scene_id or not pass_date or (scene_id, pass_date) in seen:
            continue
        seen.add((scene_id, pass_date))
        passes.append([scene_id, pass_date])

    if not passes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "no usable passes to bundle; run an analysis with at least one resolved pass first",
        )
    if len(passes) > MAX_BUNDLE_PASSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"too many passes to bundle at once (max {MAX_BUNDLE_PASSES}); narrow the date range",
        )

    # The task id IS the bundle id, so the download route reconstructs the storage key from the path
    # without any server-side mapping. Pre-generate it to derive the key before enqueueing.
    bundle_job_id = str(uuid.uuid4())
    bundle_key = aoi_bundle_zip_key(bundle_job_id)
    bundle_aoi_orthophotos_task.apply_async(
        task_id=bundle_job_id, args=[payload.geometry, passes, bundle_key]
    )
    log.info("aoi.bundle.dispatch", job_id=job_id, bundle_job_id=bundle_job_id, passes=len(passes))
    return {"bundle_job_id": bundle_job_id, "state": "queued"}


@router.get("/analyse/aoi/orthophoto-bundle/{bundle_job_id}/download")
async def orthophoto_bundle_download_endpoint(
    bundle_job_id: str,
    principal: RunAnalysisPrincipal,
) -> Response:
    """Proxy the finished all-passes orthophoto zip as a download attachment (302 to a presigned
    URL). 404 until the bundle task has written it (still running, failed, or its TTL expired).
    Requires `run_analysis`."""
    from rs_core.storage import S3CogStore, aoi_bundle_zip_key

    settings = get_settings()
    try:
        store = S3CogStore(settings)
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "object storage not available"
        ) from exc

    key = aoi_bundle_zip_key(bundle_job_id)
    if not await run_in_threadpool(store.exists, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bundle not ready or expired")

    filename = f"orthophotos_{bundle_job_id}.zip"
    url = await run_in_threadpool(store.presigned_url, key, filename=filename)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


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
        p for p in _all_passes(job_result) if p.get("status") in ("ok", "interpolated")
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
            # Interpolated passes are averaged from two source scenes. Use the custom requested date
            # as the canonical pass_date so the gateway record is saved under the custom date given.
            before: dict[str, Any] = p.get("before") or {}
            after: dict[str, Any] = p.get("after") or {}
            pass_date_str = (
                p.get("requested_date")
                or p.get("before_pass_date")
                or before.get("pass_date")
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
