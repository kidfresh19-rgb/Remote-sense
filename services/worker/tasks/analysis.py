"""Ad-hoc AOI preview analysis: the workspace "analyse this area" actions, computed through the
same engine as stored analyses but never persisted. No field is created, so it cannot collide
with gateway-owned identity (invariant 6).

Two shapes share one engine: the single most-recent pass (`analyse_aoi_task`, the map's "analyse
this AOI" button) and the multi-pass series (`analyse_aoi_series_task`, AOI Studio) - a batch of
specific calendar dates, or a months-back backfill sweep over a custom AOI. The series task is
enqueued and polled by job id rather than waited on, so a long backfill never holds an HTTP
request open.

Dates mode: when a requested date has no same-day scene the engine searches ±_INTERP_PAD_DAYS
around it and, if scenes exist on both sides, returns the average of the two nearest bracketing
passes (`status="interpolated"`). Only when no bracket exists on either side is `"no_pass"`
returned."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any

import numpy as np
from rs_analysis import analyze_index, get_index
from rs_core import CogStore, cog_store_from_settings, get_settings
from rs_core.cache import RedisJsonCache, redis_json_cache_from_settings
from rs_core.config import Settings, aoi_pass_concurrency
from rs_core.geo import canonical_geometry_hash
from rs_core.logging import get_logger
from rs_core.storage import aoi_tmp_cog_key
from rs_imagery import AOI, AccessPort, SceneRef, TimeRange, get_access_adapter

from services.worker.celery_app import celery
from services.worker.planning import backfill_window

log = get_logger("services.worker.tasks.analysis")

# Scene-level cloud pre-filter for the archive search (per-AOI SCL masking still decides clarity).
_SEARCH_CLOUD_PCT = 70.0
# Bounds so a single preview can never fan out to an unbounded number of reads. A backfill sweep
# keeps the most-recent passes up to this cap; the batch is capped at the request layer.
MAX_SERIES_PASSES = 60
# AOI sanity guard. A preview AOI is a field/estate-sized polygon, not a region: reject anything
# whose bbox spans more than this many degrees on a side (~220 km) so a geocoded province or
# country polygon can never trigger an enormous multi-scene fetch.
MAX_AOI_SPAN_DEG = 2.0
# How far either side of a requested date to search for bracketing scenes when no same-day pass
# exists. One Sentinel-2 revisit cycle (5 d) + 2 d buffer so edge dates in a sparse request
# batch reliably find neighbours even at the equatorial minimum cadence.
_INTERP_PAD_DAYS = 7
# Confidence ranks for _lower_confidence: pick the weaker of two bracketing passes.
_CONF_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


class ResultCache:
    """The per-pass result cache (ADR 0011): one scene's index stats keyed by what the pass was
    resolved from - (canonical geometry hash, index, formula_version, provider scene id) - every
    component immutable, so a re-run or an interpolation that reuses a bracket scene skips the
    fetch, the quota token, and the read entirely. The key is fully known before fetching, so a
    hit short-circuits `_analyse_scene`. Wraps a fail-open RedisJsonCache; interpolated passes are
    assembled from their two cached per-scene results, so they need no key of their own."""

    def __init__(self, cache: RedisJsonCache, *, ttl_s: int) -> None:
        self._cache = cache
        self._ttl_s = ttl_s
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(aoi: AOI, index_name: str, scene_id: str) -> str:
        formula_version = get_index(index_name).formula_version
        return f"{canonical_geometry_hash(aoi.geometry)}:{index_name}:{formula_version}:{scene_id}"

    async def get(self, aoi: AOI, index_name: str, scene_id: str) -> dict[str, Any] | None:
        value = await self._cache.get(self._key(aoi, index_name, scene_id))
        if isinstance(value, dict):
            self.hits += 1
            return value
        self.misses += 1
        return None

    async def put(self, aoi: AOI, index_name: str, scene_id: str, result: dict[str, Any]) -> None:
        await self._cache.set(self._key(aoi, index_name, scene_id), result, ttl_s=self._ttl_s)

    async def aclose(self) -> None:
        await self._cache.aclose()


def _build_result_cache(settings: Settings) -> ResultCache | None:
    """The per-pass result cache on the shared Redis, or None when no Redis URL is configured.
    Built inside the running loop (the redis.asyncio client binds to it) and closed at task end."""
    cache = redis_json_cache_from_settings(settings, namespace="aoi:result")
    if cache is None:
        return None
    return ResultCache(cache, ttl_s=settings.aoi_result_cache_ttl_s)


def _build_search_cache(settings: Settings) -> RedisJsonCache | None:
    """The STAC search cache on the shared Redis, or None when no Redis URL is configured.
    Passed to WindowedCogAdapter so a re-run skips the STAC catalog query entirely (ADR 0011).
    Built inside the running loop (the redis.asyncio client binds to it); the adapter holds the
    reference, and the task closes the result cache's client (the search cache shares the same
    Redis pool via redis_json_cache_from_settings)."""
    return redis_json_cache_from_settings(settings, namespace="aoi:search")


def _build_adapter(
    settings: Settings, *, search_cache: RedisJsonCache | None = None
) -> tuple[AccessPort, list[RedisJsonCache]]:
    """Build the configured imagery adapter plus the scene caches to close at task end. Delegates to
    the shared windowed_cog reader so the AOI Studio path attaches the same immutable cross-lane
    scene caches (cdse:scene_meta + cdse:scene_item, Phase 2a) as the stored-collection path: an AOI
    pass over a scene already touched by either lane skips the product-XML read, and an AOI preview
    warms the caches for the later field collection. The injected `search_cache` (ADR 0011) still
    skips the STAC catalog query on a repeat; it is owned and closed by the caller, so only the
    scene caches built here are returned. Other adapters (mock, server_compute) come back with no
    caches."""
    from services.worker.tasks.collection import build_windowed_cog_reader

    return build_windowed_cog_reader(settings, search_cache=search_cache)


def _band_memo_stats(adapter: AccessPort) -> dict[str, int] | None:
    """The adapter's per-task band/metadata memo hit/miss counts, or None when the active adapter
    has no read-level memo (mock, server_compute). `misses` is the real CDSE read count, so this is
    the per-run read budget the ADR 0011 gate wants surfaced. Read defensively via getattr so the
    engine stays adapter-agnostic (CLAUDE.md invariant 1)."""
    getter = getattr(adapter, "read_cache_stats", None)
    if getter is None:
        return None
    stats = getter()
    return stats if isinstance(stats, dict) else None


def _start_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _geojson_bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    """Min/max lon/lat over a GeoJSON geometry's coordinates, without importing shapely (keeps
    this module importable from the API process for the lazy enqueue path)."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    walk(geometry.get("coordinates"))
    if not xs:
        raise ValueError("geometry has no coordinates")
    return min(xs), min(ys), max(xs), max(ys)


def _guard_aoi_size(geometry: dict[str, Any]) -> None:
    min_x, min_y, max_x, max_y = _geojson_bbox(geometry)
    if max(max_x - min_x, max_y - min_y) > MAX_AOI_SPAN_DEG:
        raise ValueError(
            f"AOI is too large for a preview (over {MAX_AOI_SPAN_DEG} degrees across); "
            "draw a field-sized area."
        )


def _render_rgb_cog(
    bands: dict[str, np.ndarray],
    transform: Any,
    crs: Any,
    *,
    aoi_mask: np.ndarray | None = None,
) -> bytes:
    """Encode B04/B03/B02 reflectance bands as a georeferenced RGB COG (band order Red=B04,
    Green=B03, Blue=B02; float32 reflectance, no display stretch - the same contract as the
    registered-field RGB download, so the file opens true to value in QGIS with a 0-0.3 stretch).
    When `aoi_mask` is given, pixels outside the AOI polygon are written as NoData, so a
    non-rectangular AOI clips to its true shape rather than shipping its bounding-box window.
    Requires the `geo` extra (rasterio); raises RuntimeError when absent."""
    from rs_analysis.cog import rgb_raster, write_cog

    return write_cog(rgb_raster(bands, aoi_mask=aoi_mask), transform=transform, crs=crs)


def _aoi_window_mask(
    geometry: dict[str, Any],
    *,
    crs: str,
    transform: tuple[float, float, float, float, float, float],
    shape: tuple[int, int],
) -> np.ndarray:
    """Boolean (H, W) mask, True inside the AOI polygon, aligned to the fetched window. The AOI
    geometry (EPSG:4326) is reprojected to the band CRS before rasterizing, mirroring how the
    windowed_cog adapter masks for zonal stats, so the RGB COG clips to the drawn polygon exactly
    as the index stats do. Requires the `geo` extra (rasterio); raises RuntimeError when absent."""
    try:
        from rasterio.features import geometry_mask
        from rasterio.transform import Affine
        from rasterio.warp import transform_geom
    except ImportError as exc:  # pragma: no cover - the host has no raster stack
        raise RuntimeError("AOI masking needs the `geo` extra (rasterio)") from exc

    geom = transform_geom("EPSG:4326", crs, geometry) if crs != "EPSG:4326" else geometry
    # geometry_mask marks True OUTSIDE the polygon; invert so True == inside the AOI.
    outside = geometry_mask([geom], out_shape=shape, transform=Affine(*transform), invert=False)
    return ~outside


def _jpeg_from_cog(cog_bytes: bytes) -> bytes:
    """Render a natural-colour JPEG (512 px) from an in-memory RGB reflectance COG. The per-channel
    0-0.3 stretch matches the tiler's RGB composite range. Requires the `geo` extra (rasterio +
    rio_tiler); raises RuntimeError when absent."""
    try:
        import rasterio
        from rasterio.io import MemoryFile
        from rio_tiler.io import Reader
    except ImportError as exc:
        raise RuntimeError("raster stack not available in this context") from exc

    _RGB_RANGES = ((0.0, 0.3), (0.0, 0.3), (0.0, 0.3))
    with rasterio.Env(), MemoryFile(cog_bytes) as memfile:
        with Reader(memfile.name) as cog:
            image = cog.preview(max_size=512)
    image.rescale(in_range=_RGB_RANGES)
    return image.render(img_format="JPEG", quality=85)


async def _render_clipped_rgb_cog(
    adapter: AccessPort, scene_id: str, geometry: dict[str, Any], pass_date: str
) -> bytes:
    """Fetch B04/B03/B02 for one custom-AOI scene and render the georeferenced RGB reflectance COG
    clipped to the drawn polygon (pixels inside the fetched bounding-box window but outside the AOI
    become NoData). The scene is re-discovered by searching +/-1 day around `pass_date` and matching
    by `scene_id`. Shared by the single-pass natural-colour render and the all-passes bundle so both
    write byte-identical COGs to `aoi_rgb_cog_key`. Requires the `geo` extra (rasterio)."""
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    target = date.fromisoformat(pass_date)
    search_range = TimeRange(
        start=_start_of_day(target) - timedelta(days=1),
        end=_start_of_day(target) + timedelta(days=2),
    )
    scenes = await adapter.search(aoi, search_range, max_scene_cloud_pct=100.0)
    scene = next((s for s in scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise ValueError(f"scene {scene_id!r} not found around {pass_date!r}")
    fetched = await adapter.fetch(
        scene, aoi, bands=sorted(["B02", "B03", "B04"]), resolution_m=10.0
    )
    height, width = next(iter(fetched.data.bands.values())).shape
    mask = _aoi_window_mask(
        geometry, crs=fetched.data.crs, transform=fetched.data.transform, shape=(height, width)
    )
    return _render_rgb_cog(
        fetched.data.bands, fetched.data.transform, fetched.data.crs, aoi_mask=mask
    )


async def _analyse_scene(
    adapter: AccessPort,
    scene: SceneRef,
    aoi: AOI,
    index_name: str,
    *,
    result_cache: ResultCache | None = None,
    cog_store: CogStore | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """One index over one scene for an arbitrary AOI, as a JSON-safe pass-result dict. Fetches at
    the index's native resolution and lets `analyze_index` apply reflectance + SCL masking
    (invariants 2-4); nothing is persisted. Full provenance is included so 'ok' passes can be
    converted to IndexResult for gateway push without a second fetch. With a `result_cache`, a hit
    returns the stored pass without fetching (ADR 0011) - the key is immutable scene math.

    When `cog_store` and `job_id` are provided (single-index AOI/farm series tasks), an index COG
    is emitted to `aoi_tmp/{job_id}/{pass_date}/{index}.tif` after the stats are assembled. Cache
    hits skip both the fetch and the COG (the arrays are gone). Failure is isolated: a put error
    logs a warning but never fails the pass."""
    if result_cache is not None:
        cached = await result_cache.get(aoi, index_name, scene.scene_id)
        if cached is not None:
            log.info(
                "result_cache_hit",
                index=index_name,
                scene_id=scene.scene_id,
                pass_date=cached.get("pass_date"),
            )
            return cached
        log.info("result_cache_miss", index=index_name, scene_id=scene.scene_id)
    spec = get_index(index_name)
    fetched = await adapter.fetch(
        scene, aoi, bands=sorted(spec.bands), resolution_m=float(spec.resolution_m)
    )
    out = analyze_index(
        reflectance=fetched.data.bands,
        index_name=index_name,
        resolution_m=int(fetched.data.resolution_m),
        clear_fraction_override=fetched.clear_fraction,
    )
    s = out.stats
    result = {
        "status": "ok",
        "index": out.index_name,
        "pass_date": scene.sensing_datetime.date().isoformat(),
        "scene_id": scene.scene_id,
        "formula_version": out.formula_version,
        "provider": fetched.provenance.provider,
        "provider_scene_id": fetched.provenance.provider_scene_id,
        "processing_mode": str(fetched.provenance.processing_mode),
        "mean": s.mean,
        "min": s.min,
        "max": s.max,
        "p10": s.p10,
        "p90": s.p90,
        "clear_fraction": out.clear_fraction,
        "confidence": out.confidence,
        "resolution_m": out.resolution_m,
        "pixels": s.count,
    }
    if result_cache is not None:
        await result_cache.put(aoi, index_name, scene.scene_id, result)

    # Emit temp COG for AOI Studio downloads (failure-isolated; arrays in memory now).
    if cog_store is not None and job_id is not None:
        try:
            from rs_analysis.cog import index_raster as _ir
            from rs_analysis.cog import write_cog as _wc

            pass_date_str = scene.sensing_datetime.date().isoformat()
            key = aoi_tmp_cog_key(job_id, pass_date_str, index_name)
            arr = _ir(fetched.data.bands, index_name)
            t, c = fetched.data.transform, fetched.data.crs

            def _put() -> None:
                cog_store.put(key, _wc(arr, transform=t, crs=c))

            await asyncio.to_thread(_put)
        except Exception:
            log.warning("aoi.tmp_cog.failed", job_id=job_id, index=index_name, exc_info=True)

    return result


async def _clearest_scene_result(
    adapter: AccessPort,
    scenes: list[SceneRef],
    aoi: AOI,
    index_name: str,
    *,
    result_cache: ResultCache | None = None,
    cog_store: CogStore | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """The clearest pass among `scenes` (more than one can land on the same day). Computes each and
    keeps the highest clear-pixel fraction."""
    best: dict[str, Any] | None = None
    for scene in scenes:
        result = await _analyse_scene(
            adapter,
            scene,
            aoi,
            index_name,
            result_cache=result_cache,
            cog_store=cog_store,
            job_id=job_id,
        )
        if best is None or result["clear_fraction"] > best["clear_fraction"]:
            best = result
    assert best is not None  # callers only pass a non-empty list
    return best


def _bracket_passes(
    target: date, by_day: dict[date, list[SceneRef]]
) -> tuple[date | None, date | None]:
    """Nearest scene dates strictly before and strictly after `target` in `by_day`."""
    days = sorted(by_day)
    before = next((d for d in reversed(days) if d < target), None)
    after = next((d for d in days if d > target), None)
    return before, after


def _avg_stat(*vals: float | None) -> float | None:
    valid = [v for v in vals if v is not None]
    return sum(valid) / len(valid) if valid else None


def _lower_confidence(a: str | None, b: str | None) -> str | None:
    """The weaker of two confidence labels (conservative: never overstate certainty)."""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a if _CONF_RANK.get(a, 0) <= _CONF_RANK.get(b, 0) else b


async def _interpolate_result(
    adapter: AccessPort,
    before_scenes: list[SceneRef],
    after_scenes: list[SceneRef],
    aoi: AOI,
    index_name: str,
    requested: date,
    *,
    result_cache: ResultCache | None = None,
) -> dict[str, Any]:
    """Average two bracketing passes for a date that has no same-day scene. Picks the clearest
    scene on each side, averages all index stats, and takes the weaker confidence + coarser
    resolution so the result is never more certain than the underlying data warrants. Each side's
    per-scene result flows through the result cache, so an interpolated pass needs no key of its
    own (ADR 0011)."""
    before = await _clearest_scene_result(
        adapter, before_scenes, aoi, index_name, result_cache=result_cache
    )
    after = await _clearest_scene_result(
        adapter, after_scenes, aoi, index_name, result_cache=result_cache
    )
    avg_clear = _avg_stat(before.get("clear_fraction", 0.0), after.get("clear_fraction", 0.0))
    return {
        "status": "interpolated",
        "index": before["index"],
        "requested_date": requested.isoformat(),
        "before_pass_date": before["pass_date"],
        "after_pass_date": after["pass_date"],
        "mean": _avg_stat(before.get("mean"), after.get("mean")),
        "min": _avg_stat(before.get("min"), after.get("min")),
        "max": _avg_stat(before.get("max"), after.get("max")),
        "p10": _avg_stat(before.get("p10"), after.get("p10")),
        "p90": _avg_stat(before.get("p90"), after.get("p90")),
        "clear_fraction": avg_clear or 0.0,
        "confidence": _lower_confidence(before.get("confidence"), after.get("confidence")),
        "resolution_m": max(before.get("resolution_m", 10), after.get("resolution_m", 10)),
    }


async def _analyse_aoi(
    geometry: dict[str, object],
    index_name: str,
    *,
    adapter: AccessPort | None = None,
    result_cache: ResultCache | None = None,
) -> dict[str, object]:
    """Compute one index over an arbitrary AOI for its most recent usably-clear pass, without
    persisting anything. Only the few most-recent scenes are fetched (not the whole window) so a
    single click never fans out to dozens of reads, and the clearest of them is returned. Shares
    the per-pass result cache, so a repeat click on the same AOI is served without a fetch.

    `adapter` is injectable so the task wrapper can pass a WindowedCogAdapter with a search cache
    (ADR 0011); defaults to `get_access_adapter` when not provided."""
    if adapter is None:
        adapter = get_access_adapter(get_settings())
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    now = datetime.now(UTC)
    scenes = await adapter.search(
        aoi,
        TimeRange(start=now - timedelta(days=90), end=now),
        max_scene_cloud_pct=_SEARCH_CLOUD_PCT,
    )
    if not scenes:
        return {"status": "no_scenes"}

    best: dict[str, Any] | None = None
    for scene in sorted(scenes, key=lambda s: s.sensing_datetime, reverse=True)[:3]:
        result = await _analyse_scene(adapter, scene, aoi, index_name, result_cache=result_cache)
        if best is None or result["clear_fraction"] > best["clear_fraction"]:
            best = result
        if result["clear_fraction"] >= 0.6:  # clear enough; stop early to stay responsive
            break

    assert best is not None
    return best


def _isolated(
    factory: Callable[[], Awaitable[dict[str, Any]]], label: dict[str, Any]
) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Wrap one pass factory so a failed read becomes an `error` pass instead of sinking the whole
    multi-pass job - 3a applied at the AOI Studio orchestration layer: one cold-archived / forbidden
    scene must not lose every other resolved pass in the series. `label` carries the pass's identity
    (index + scene_id or requested_date) so the results console can show which pass failed; an error
    pass is naturally excluded from the chart, the gateway push, and the `resolved` count, which all
    key off status ok/interpolated. CancelledError (a BaseException) is not caught, so task
    cancellation still propagates."""

    async def run() -> dict[str, Any]:
        try:
            return await factory()
        except Exception as exc:
            log.warning("aoi.pass.failed", **label, detail=str(exc), exc_info=True)
            return {"status": "error", "detail": str(exc), **label}

    return run


async def _gather_passes(
    factories: list[Callable[[], Awaitable[dict[str, Any]]]],
    *,
    concurrency: int,
    on_progress: Callable[[int, int], None] | None,
) -> list[dict[str, Any]]:
    """Run per-pass coroutine factories concurrently under a bounded semaphore (ADR 0011),
    returning results in the same order as `factories` so requested / oldest-first ordering is
    preserved. Progress fires as each pass settles (not in dispatch order), so {done} climbs
    monotonically to len(factories). The semaphore bounds concurrent passes; the scene reads
    inside one pass stay sequential."""
    total = len(factories)
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def run(factory: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        nonlocal done
        async with semaphore:
            result = await factory()
        done += 1
        if on_progress:
            on_progress(done, total)
        return result

    return list(await asyncio.gather(*(run(f) for f in factories)))


async def _resolve_requested_day(
    adapter: AccessPort,
    by_day: dict[date, list[SceneRef]],
    aoi: AOI,
    index_name: str,
    day: date,
    *,
    result_cache: ResultCache | None = None,
    cog_store: CogStore | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """One requested calendar date -> its pass dict: the clearest same-day scene, else the
    average of the two nearest bracketing passes, else no_pass. Extracted so it can be dispatched
    concurrently per date (ADR 0011). COG emission is only for exact same-day passes (not
    interpolated), matching the `aoi_tmp` key scheme which uses the actual sensing date."""
    same_day = by_day.get(day)
    if same_day:
        result = await _clearest_scene_result(
            adapter,
            same_day,
            aoi,
            index_name,
            result_cache=result_cache,
            cog_store=cog_store,
            job_id=job_id,
        )
        result["requested_date"] = day.isoformat()
        return result
    before_date, after_date = _bracket_passes(day, by_day)
    if before_date is not None and after_date is not None:
        # Interpolated passes: no COG emission (no single scene to store)
        return await _interpolate_result(
            adapter,
            by_day[before_date],
            by_day[after_date],
            aoi,
            index_name,
            day,
            result_cache=result_cache,
        )
    return {"requested_date": day.isoformat(), "status": "no_pass"}


async def _analyse_aoi_series(
    geometry: dict[str, Any],
    index_name: str,
    mode: str,
    dates: list[str] | None,
    months: int | None,
    *,
    adapter: AccessPort | None = None,
    backfill_months: int | None = None,
    now: datetime | None = None,
    concurrency: int | None = None,
    result_cache: ResultCache | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cog_store: CogStore | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Multi-pass preview over a custom AOI (AOI Studio). `mode="dates"` resolves each requested
    calendar date to its same-day scene; when none exists the two nearest bracketing passes are
    averaged and returned as `status="interpolated"`. Only when no bracket exists on either side
    is `"no_pass"` returned. `mode="backfill"` sweeps the months-back window and returns every
    usable pass up to `MAX_SERIES_PASSES`. Passes run concurrently under a bounded semaphore
    (ADR 0011) - the quota bucket is the real ceiling - while results stay in requested /
    oldest-first order. Each pass runs through the production engine and nothing is persisted
    (invariant 6). `on_progress(done, total)` fires as each pass settles so the task can publish a
    job progress meter.

    `adapter`, `backfill_months`, `now`, `concurrency`, and `result_cache` are injectable so the
    engine is testable against the mock adapter with no network (CLAUDE.md 3); the task resolves
    them from settings."""
    _guard_aoi_size(geometry)
    t0 = time.monotonic()
    get_index(index_name)  # validate up front; KeyError surfaces as a task failure
    if adapter is None or backfill_months is None or concurrency is None:
        settings = get_settings()
        adapter = adapter or get_access_adapter(settings)
        if backfill_months is None:
            backfill_months = settings.backfill_months
        if concurrency is None:
            concurrency = aoi_pass_concurrency(settings)
    assert adapter is not None and backfill_months is not None and concurrency is not None
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    now = now or datetime.now(UTC)

    if mode == "dates":
        requested = sorted({date.fromisoformat(d) for d in (dates or [])})
        if not requested:
            raise ValueError("dates mode needs at least one date")
        # Pad the window by _INTERP_PAD_DAYS on each side so bracketing scenes for edge dates
        # are included in by_day even when no same-day pass exists.
        search_range = TimeRange(
            start=_start_of_day(requested[0]) - timedelta(days=_INTERP_PAD_DAYS),
            end=_start_of_day(requested[-1]) + timedelta(days=1 + _INTERP_PAD_DAYS),
        )
        scenes = await adapter.search(aoi, search_range, max_scene_cloud_pct=_SEARCH_CLOUD_PCT)
        by_day: dict[date, list[SceneRef]] = defaultdict(list)
        for scene in scenes:
            by_day[scene.sensing_datetime.date()].append(scene)

        passes = await _gather_passes(
            [
                _isolated(
                    partial(
                        _resolve_requested_day,
                        adapter,
                        by_day,
                        aoi,
                        index_name,
                        day,
                        result_cache=result_cache,
                        cog_store=cog_store,
                        job_id=job_id,
                    ),
                    {"index": index_name, "requested_date": day.isoformat()},
                )
                for day in requested
            ],
            concurrency=concurrency,
            on_progress=on_progress,
        )

    elif mode == "backfill":
        depth = max(1, min(int(months or backfill_months), backfill_months))
        window_start, _ = backfill_window(now.date(), depth)
        scenes = await adapter.search(
            aoi,
            TimeRange(start=_start_of_day(window_start), end=now),
            max_scene_cloud_pct=_SEARCH_CLOUD_PCT,
        )
        # Keep the most recent passes when the window holds more than the cap, then present
        # oldest-first so the series reads left to right.
        capped = sorted(scenes, key=lambda s: s.sensing_datetime, reverse=True)[:MAX_SERIES_PASSES]
        chosen = sorted(capped, key=lambda s: s.sensing_datetime)

        passes = await _gather_passes(
            [
                _isolated(
                    partial(
                        _analyse_scene,
                        adapter,
                        scene,
                        aoi,
                        index_name,
                        result_cache=result_cache,
                        cog_store=cog_store,
                        job_id=job_id,
                    ),
                    {
                        "index": index_name,
                        "scene_id": scene.scene_id,
                        "pass_date": scene.sensing_datetime.date().isoformat(),
                    },
                )
                for scene in chosen
            ],
            concurrency=concurrency,
            on_progress=on_progress,
        )

    else:
        raise ValueError(f"unknown AOI series mode {mode!r}")

    res = {
        "status": "ok",
        "index": index_name,
        "mode": mode,
        "requested": len(passes),
        "resolved": sum(1 for p in passes if p.get("status") in ("ok", "interpolated")),
        "passes": passes,
    }
    band_stats = _band_memo_stats(adapter)
    log.info(
        "aoi.series.complete",
        index=index_name,
        mode=mode,
        requested=res["requested"],
        resolved=res["resolved"],
        n_passes=len(passes),
        wall_clock_s=round(time.monotonic() - t0, 2),
        result_cache_hits=result_cache.hits if result_cache is not None else 0,
        result_cache_misses=result_cache.misses if result_cache is not None else 0,
        band_memo_hits=band_stats["hits"] if band_stats is not None else None,
        band_memo_misses=band_stats["misses"] if band_stats is not None else None,
    )
    return res


async def _analyse_aoi_series_multi(
    geometry: dict[str, Any],
    index_names: list[str],
    mode: str,
    dates: list[str] | None,
    months: int | None,
    *,
    adapter: AccessPort | None = None,
    backfill_months: int | None = None,
    now: datetime | None = None,
    concurrency: int | None = None,
    result_cache: ResultCache | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """All-indices AOI Studio preview in one task (ADR 0011 Phase 2). The single-index engine is
    fanned out across `index_names` over one shared search, one bounded gather, one adapter (one
    in-process band memo), and one result cache, so a scene's overlapping bands (B04/B08 ...) are
    read once across every index instead of once per index, and the CDSE quota bucket is contended
    once, not per index. Nothing is persisted (invariant 6); the band memo stays per-task and is
    dropped at task end (invariant 7). Returns ``{"status", "mode", "indices": {name: result}}``
    where each per-index value is the same shape `_analyse_aoi_series` returns.

    `adapter`, `backfill_months`, `now`, `concurrency`, and `result_cache` are injectable for the
    zero-network mock tests (CLAUDE.md 3); the task resolves them from settings."""
    _guard_aoi_size(geometry)
    t0 = time.monotonic()
    if not index_names:
        raise ValueError("at least one index is required")
    for name in index_names:
        get_index(name)  # validate every name up front; KeyError surfaces as a task failure
    if adapter is None or backfill_months is None or concurrency is None:
        settings = get_settings()
        adapter = adapter or get_access_adapter(settings)
        if backfill_months is None:
            backfill_months = settings.backfill_months
        if concurrency is None:
            concurrency = aoi_pass_concurrency(settings)
    assert adapter is not None and backfill_months is not None and concurrency is not None
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    now = now or datetime.now(UTC)

    # One search, independent of index; pass factories are then the cross product of indices and
    # the resolved days/scenes so the band memo dedupes shared reads across indices.
    index_factories: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = []

    if mode == "dates":
        requested = sorted({date.fromisoformat(d) for d in (dates or [])})
        if not requested:
            raise ValueError("dates mode needs at least one date")
        search_range = TimeRange(
            start=_start_of_day(requested[0]) - timedelta(days=_INTERP_PAD_DAYS),
            end=_start_of_day(requested[-1]) + timedelta(days=1 + _INTERP_PAD_DAYS),
        )
        scenes = await adapter.search(aoi, search_range, max_scene_cloud_pct=_SEARCH_CLOUD_PCT)
        by_day: dict[date, list[SceneRef]] = defaultdict(list)
        for scene in scenes:
            by_day[scene.sensing_datetime.date()].append(scene)
        for index_name in index_names:
            for day in requested:
                index_factories.append(
                    (
                        index_name,
                        _isolated(
                            partial(
                                _resolve_requested_day,
                                adapter,
                                by_day,
                                aoi,
                                index_name,
                                day,
                                result_cache=result_cache,
                            ),
                            {"index": index_name, "requested_date": day.isoformat()},
                        ),
                    )
                )

    elif mode == "backfill":
        depth = max(1, min(int(months or backfill_months), backfill_months))
        window_start, _ = backfill_window(now.date(), depth)
        scenes = await adapter.search(
            aoi,
            TimeRange(start=_start_of_day(window_start), end=now),
            max_scene_cloud_pct=_SEARCH_CLOUD_PCT,
        )
        capped = sorted(scenes, key=lambda s: s.sensing_datetime, reverse=True)[:MAX_SERIES_PASSES]
        chosen = sorted(capped, key=lambda s: s.sensing_datetime)
        for index_name in index_names:
            for scene in chosen:
                index_factories.append(
                    (
                        index_name,
                        _isolated(
                            partial(
                                _analyse_scene,
                                adapter,
                                scene,
                                aoi,
                                index_name,
                                result_cache=result_cache,
                            ),
                            {
                                "index": index_name,
                                "scene_id": scene.scene_id,
                                "pass_date": scene.sensing_datetime.date().isoformat(),
                            },
                        ),
                    )
                )

    else:
        raise ValueError(f"unknown AOI series mode {mode!r}")

    flat = await _gather_passes(
        [factory for _, factory in index_factories],
        concurrency=concurrency,
        on_progress=on_progress,
    )

    # Regroup flat results back to their index, preserving the requested / oldest-first order
    # _gather_passes guarantees (results come back in factory order).
    by_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (index_name, _), pass_result in zip(index_factories, flat, strict=True):
        by_index[index_name].append(pass_result)

    indices_out: dict[str, Any] = {}
    for index_name in index_names:
        passes = by_index[index_name]
        indices_out[index_name] = {
            "status": "ok",
            "index": index_name,
            "mode": mode,
            "requested": len(passes),
            "resolved": sum(1 for p in passes if p.get("status") in ("ok", "interpolated")),
            "passes": passes,
        }

    band_stats = _band_memo_stats(adapter)
    log.info(
        "aoi.series.multi.complete",
        indices=index_names,
        n_indices=len(index_names),
        mode=mode,
        n_passes=len(flat),
        wall_clock_s=round(time.monotonic() - t0, 2),
        result_cache_hits=result_cache.hits if result_cache is not None else 0,
        result_cache_misses=result_cache.misses if result_cache is not None else 0,
        band_memo_hits=band_stats["hits"] if band_stats is not None else None,
        band_memo_misses=band_stats["misses"] if band_stats is not None else None,
    )
    return {"status": "ok", "mode": mode, "indices": indices_out}


@celery.task(name="analysis.analyse_aoi")
def analyse_aoi_task(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Ad-hoc preview analysis over a custom AOI (the workspace "analyse this area" action): the
    most recent usable pass's index stats, computed through the same engine as stored analyses but
    never persisted. No field is created, so it cannot collide with gateway-owned identity
    (invariant 6)."""
    settings = get_settings()

    async def _runner() -> dict[str, object]:
        result_cache = _build_result_cache(settings)
        search_cache = _build_search_cache(settings)
        adapter, scene_caches = _build_adapter(settings, search_cache=search_cache)
        try:
            return await _analyse_aoi(
                geometry, index_name, adapter=adapter, result_cache=result_cache
            )
        finally:
            for scene_cache in scene_caches:
                await scene_cache.aclose()
            if result_cache is not None:
                await result_cache.aclose()
            if search_cache is not None:
                await search_cache.aclose()

    return asyncio.run(_runner())


@celery.task(name="analysis.render_natural_color")
def render_natural_color_task(
    scene_id: str,
    geometry: dict[str, Any],
    pass_date: str,
    cache_key: str,
    cog_cache_key: str | None = None,
) -> str:
    """Render the natural-colour artifacts for one custom AOI scene (B04/B03/B02) and cache them in
    MinIO under the `aoi_preview/` prefix (7-day lifecycle): always the 512 px JPEG at `cache_key`,
    and - when `cog_cache_key` is given - the georeferenced RGB reflectance COG it was rendered
    from, so the GeoTIFF download is a cache hit once the thumbnail has been viewed. Returns a
    base64-encoded JPEG string (JSON-safe for the Redis result backend). The scene is re-discovered
    by searching ±1 day around `pass_date` and matching by `scene_id`. Each cache write is
    failure-isolated: a put error logs a warning and does not fail the task."""
    import base64

    settings = get_settings()

    async def _run() -> bytes:
        adapter = get_access_adapter(settings)
        return await _render_clipped_rgb_cog(adapter, scene_id, geometry, pass_date)

    cog_bytes = asyncio.run(_run())
    jpeg_bytes = _jpeg_from_cog(cog_bytes)

    # Caching is best-effort: a store that cannot even be built (misconfigured S3) must still let
    # the task return the rendered JPEG, exactly as the put failures below degrade rather than fail.
    try:
        store = cog_store_from_settings(settings)
    except Exception:
        log.warning("natural_color.cache_store.failed", scene_id=scene_id, exc_info=True)
        store = None
    if store is not None:
        try:
            store.put(cache_key, jpeg_bytes, content_type="image/jpeg")
        except Exception:
            log.warning("natural_color.cache_put.failed", scene_id=scene_id, exc_info=True)
        if cog_cache_key is not None:
            try:
                store.put(cog_cache_key, cog_bytes, content_type="image/tiff")
            except Exception:
                log.warning("natural_color.cog_cache_put.failed", scene_id=scene_id, exc_info=True)

    return base64.b64encode(jpeg_bytes).decode()


def _run_aoi_series(
    settings: Settings,
    concurrency: int,
    make_coro: Callable[[AccessPort, ResultCache | None], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run an AOI-series coroutine on a fresh event loop whose default thread-pool executor is
    sized to `concurrency`, so the asyncio.to_thread CDSE reads inside WindowedCogAdapter.fetch
    are bounded by the pass semaphore and not throttled by the default pool (min(32, cpu + 4),
    which can be smaller on a low-core worker). The per-pass result cache and search cache are built
    inside the loop (their redis.asyncio clients bind to it) and closed at task end. asyncio.run
    shuts the executor down with the loop."""

    async def _runner() -> dict[str, Any]:
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="aoi-read")
        )
        result_cache = _build_result_cache(settings)
        search_cache = _build_search_cache(settings)
        adapter, scene_caches = _build_adapter(settings, search_cache=search_cache)
        try:
            return await make_coro(adapter, result_cache)
        finally:
            for scene_cache in scene_caches:
                await scene_cache.aclose()
            if result_cache is not None:
                await result_cache.aclose()
            if search_cache is not None:
                await search_cache.aclose()

    return asyncio.run(_runner())


@celery.task(bind=True, name="analysis.analyse_aoi_series")
def analyse_aoi_series_task(
    self: Any,
    geometry: dict[str, Any],
    index_name: str,
    mode: str,
    dates: list[str] | None = None,
    months: int | None = None,
) -> dict[str, Any]:
    """AOI Studio's multi-pass preview (batch of dates, or a months-back backfill sweep over a
    custom AOI). Enqueued and polled by job id - it can run for minutes - and publishes a
    `{done, total}` progress meter as each pass settles. Never persists (invariant 6).
    Emits a temp index COG per pass to `aoi_tmp/{job_id}/{pass_date}/{index}.tif` (24-h TTL)
    so analysts can download the spatial raster for each pass."""

    def on_progress(done: int, total: int) -> None:
        self.update_state(state="PROGRESS", meta={"done": done, "total": total})

    settings = get_settings()
    concurrency = aoi_pass_concurrency(settings)
    cog_store = cog_store_from_settings(settings)
    job_id: str = self.request.id
    return _run_aoi_series(
        settings,
        concurrency,
        lambda adapter, rc: _analyse_aoi_series(
            geometry,
            index_name,
            mode,
            dates,
            months,
            adapter=adapter,
            concurrency=concurrency,
            result_cache=rc,
            on_progress=on_progress,
            cog_store=cog_store,
            job_id=job_id,
        ),
    )


@celery.task(bind=True, name="analysis.analyse_aoi_series_multi")
def analyse_aoi_series_multi_task(
    self: Any,
    geometry: dict[str, Any],
    index_names: list[str],
    mode: str,
    dates: list[str] | None = None,
    months: int | None = None,
) -> dict[str, Any]:
    """AOI Studio's all-indices preview in one job (ADR 0011 Phase 2). Same enqueue/poll/progress
    shape as `analyse_aoi_series_task`, but every index shares one search, one bounded gather, one
    adapter (one band memo), and one result cache, so shared bands are read once across indices.
    Never persists (invariant 6). Returns ``{"status","mode","indices": {name: series-result}}``."""

    def on_progress(done: int, total: int) -> None:
        self.update_state(state="PROGRESS", meta={"done": done, "total": total})

    settings = get_settings()
    concurrency = aoi_pass_concurrency(settings)
    return _run_aoi_series(
        settings,
        concurrency,
        lambda adapter, rc: _analyse_aoi_series_multi(
            geometry,
            index_names,
            mode,
            dates,
            months,
            adapter=adapter,
            concurrency=concurrency,
            result_cache=rc,
            on_progress=on_progress,
        ),
    )


# ---------------------------------------------------------------------------
# Farm AOI series: union of field geometries → same engine
# ---------------------------------------------------------------------------


def _union_geometries(geometries: list[dict[str, Any]]) -> dict[str, Any]:
    """Union a list of GeoJSON Polygon / MultiPolygon geometries into a single GeoJSON geometry
    (Polygon or MultiPolygon). Uses shapely for the union; importable from the API process because
    shapely is in the base deps (no rasterio required).

    Raises ValueError when the list is empty (a farm with no fields cannot produce an AOI)."""
    if not geometries:
        raise ValueError("at least one field geometry is required to construct a farm AOI")

    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    shapes = [shape(g) for g in geometries]
    merged = unary_union(shapes)
    return dict(mapping(merged))


async def _analyse_farm_series(
    fields: list[dict[str, Any]],
    index_name: str,
    mode: str,
    dates: list[str] | None,
    months: int | None,
    *,
    adapter: AccessPort | None = None,
    backfill_months: int | None = None,
    now: datetime | None = None,
    concurrency: int | None = None,
    result_cache: ResultCache | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cog_store: CogStore | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Multi-pass preview for an entire farm: resolves the union of all field geometries and
    delegates to `_analyse_aoi_series`. `fields` is a list of ``{"field_id": str, "geometry":
    dict}`` dicts (DB rows already fetched by the Celery task or injected by tests).

    Raises ValueError when `fields` is empty (no field geometry → no AOI)."""
    if not fields:
        raise ValueError("at least one field geometry is required to construct a farm AOI")

    union_geom = _union_geometries([f["geometry"] for f in fields])
    return await _analyse_aoi_series(
        union_geom,
        index_name,
        mode,
        dates,
        months,
        adapter=adapter,
        backfill_months=backfill_months,
        now=now,
        concurrency=concurrency,
        result_cache=result_cache,
        on_progress=on_progress,
        cog_store=cog_store,
        job_id=job_id,
    )


async def _analyse_farm_series_multi(
    fields: list[dict[str, Any]],
    index_names: list[str],
    mode: str,
    dates: list[str] | None,
    months: int | None,
    *,
    adapter: AccessPort | None = None,
    backfill_months: int | None = None,
    now: datetime | None = None,
    concurrency: int | None = None,
    result_cache: ResultCache | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """All-indices farm preview (ADR 0011 Phase 2): unions the field geometries and delegates to
    `_analyse_aoi_series_multi`. `fields` mirrors `_analyse_farm_series`. Raises ValueError when
    `fields` is empty (no field geometry → no AOI)."""
    if not fields:
        raise ValueError("at least one field geometry is required to construct a farm AOI")

    union_geom = _union_geometries([f["geometry"] for f in fields])
    return await _analyse_aoi_series_multi(
        union_geom,
        index_names,
        mode,
        dates,
        months,
        adapter=adapter,
        backfill_months=backfill_months,
        now=now,
        concurrency=concurrency,
        result_cache=result_cache,
        on_progress=on_progress,
    )


def _run_farm_series(
    canonical_farm_id: str,
    make_coro: Callable[
        [list[dict[str, Any]], AccessPort, ResultCache | None, int], Awaitable[dict[str, Any]]
    ],
) -> dict[str, Any]:
    """Shared farm-series runner for the single- and all-indices tasks. A fresh loop with a
    concurrency-sized executor (so the to_thread CDSE reads are bounded by the pass semaphore, not
    the default pool), the farm's field geometries fetched over a per-task NullPool engine (forked
    workers never share a pool across loops, as in collect_pass_task), and the result + search
    caches built inside the loop and closed at task end. `make_coro` receives the fetched fields,
    the adapter, the result cache, and the resolved concurrency."""

    async def _run() -> dict[str, Any]:
        from geoalchemy2.shape import to_shape
        from rs_core.models import Farm, Field
        from shapely.geometry import mapping as _mapping
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        settings = get_settings()
        concurrency = aoi_pass_concurrency(settings)
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="aoi-read")
        )
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                farm_row = (
                    await session.execute(
                        select(Farm).where(Farm.canonical_farm_id == canonical_farm_id)
                    )
                ).scalar_one_or_none()
                if farm_row is None:
                    raise ValueError(f"farm {canonical_farm_id!r} not found")

                field_rows = (
                    (await session.execute(select(Field).where(Field.farm_id == farm_row.id)))
                    .scalars()
                    .all()
                )
        finally:
            await engine.dispose()

        fields: list[dict[str, Any]] = [
            {
                "field_id": str(f.id),
                "geometry": dict(_mapping(to_shape(f.boundary))),
            }
            for f in field_rows
            if f.boundary is not None
        ]

        result_cache = _build_result_cache(settings)
        search_cache = _build_search_cache(settings)
        adapter, scene_caches = _build_adapter(settings, search_cache=search_cache)
        try:
            return await make_coro(fields, adapter, result_cache, concurrency)
        finally:
            for scene_cache in scene_caches:
                await scene_cache.aclose()
            if result_cache is not None:
                await result_cache.aclose()
            if search_cache is not None:
                await search_cache.aclose()

    return asyncio.run(_run())


@celery.task(bind=True, name="analysis.analyse_farm_series")
def analyse_farm_series_task(
    self: Any,
    canonical_farm_id: str,
    index_name: str,
    mode: str,
    dates: list[str] | None = None,
    months: int | None = None,
) -> dict[str, Any]:
    """Farm-level AOI Studio series: fetches the farm's field geometries from the DB, unions them
    into a single AOI on the worker, and runs the same multi-pass engine as
    `analyse_aoi_series_task`. Polled by job id; publishes a `{done, total}` progress meter per
    pass. Never persists (invariant 6). Emits a temp index COG per pass (24-h TTL) so farm-level
    passes are also downloadable from the AOI Studio results console."""

    def on_progress(done: int, total: int) -> None:
        self.update_state(state="PROGRESS", meta={"done": done, "total": total})

    cog_store = cog_store_from_settings(get_settings())
    job_id: str = self.request.id
    return _run_farm_series(
        canonical_farm_id,
        lambda fields, adapter, rc, concurrency: _analyse_farm_series(
            fields,
            index_name,
            mode,
            dates,
            months,
            adapter=adapter,
            concurrency=concurrency,
            result_cache=rc,
            on_progress=on_progress,
            cog_store=cog_store,
            job_id=job_id,
        ),
    )


@celery.task(bind=True, name="analysis.analyse_farm_series_multi")
def analyse_farm_series_multi_task(
    self: Any,
    canonical_farm_id: str,
    index_names: list[str],
    mode: str,
    dates: list[str] | None = None,
    months: int | None = None,
) -> dict[str, Any]:
    """Farm-level all-indices AOI Studio preview in one job (ADR 0011 Phase 2). Same DB-fetch and
    union as `analyse_farm_series_task`, but runs every index through the shared multi engine so a
    scene's bands are read once across indices. Never persists (invariant 6)."""

    def on_progress(done: int, total: int) -> None:
        self.update_state(state="PROGRESS", meta={"done": done, "total": total})

    return _run_farm_series(
        canonical_farm_id,
        lambda fields, adapter, rc, concurrency: _analyse_farm_series_multi(
            fields,
            index_names,
            mode,
            dates,
            months,
            adapter=adapter,
            concurrency=concurrency,
            result_cache=rc,
            on_progress=on_progress,
        ),
    )
