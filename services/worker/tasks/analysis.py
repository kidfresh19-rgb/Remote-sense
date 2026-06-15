"""Ad-hoc AOI preview analysis: the workspace "analyse this area" actions, computed through the
same engine as stored analyses but never persisted. No field is created, so it cannot collide
with gateway-owned identity (invariant 6).

Two shapes share one engine: the single most-recent pass (`analyse_aoi_task`, the map's "analyse
this AOI" button) and the multi-pass series (`analyse_aoi_series_task`, AOI Studio) - a batch of
specific calendar dates, or a months-back backfill sweep over a custom AOI. The series task is
enqueued and polled by job id rather than waited on, so a long backfill never holds an HTTP
request open."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from rs_analysis import analyze_index, get_index
from rs_core import get_settings
from rs_imagery import AOI, AccessPort, SceneRef, TimeRange, get_access_adapter

from services.worker.celery_app import celery
from services.worker.planning import backfill_window

# Scene-level cloud pre-filter for the archive search (per-AOI SCL masking still decides clarity).
_SEARCH_CLOUD_PCT = 70.0
# Bounds so a single preview can never fan out to an unbounded number of reads. A backfill sweep
# keeps the most-recent passes up to this cap; the batch is capped at the request layer.
MAX_SERIES_PASSES = 60
# AOI sanity guard. A preview AOI is a field/estate-sized polygon, not a region: reject anything
# whose bbox spans more than this many degrees on a side (~220 km) so a geocoded province or
# country polygon can never trigger an enormous multi-scene fetch.
MAX_AOI_SPAN_DEG = 2.0


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


async def _analyse_scene(
    adapter: AccessPort, scene: SceneRef, aoi: AOI, index_name: str
) -> dict[str, Any]:
    """One index over one scene for an arbitrary AOI, as a JSON-safe pass-result dict. Fetches at
    the index's native resolution and lets `analyze_index` apply reflectance + SCL masking
    (invariants 2-4); nothing is persisted."""
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
    return {
        "status": "ok",
        "index": out.index_name,
        "pass_date": scene.sensing_datetime.date().isoformat(),
        "scene_id": scene.scene_id,
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


async def _clearest_scene_result(
    adapter: AccessPort, scenes: list[SceneRef], aoi: AOI, index_name: str
) -> dict[str, Any]:
    """The clearest pass among `scenes` (more than one can land on the same day). Computes each and
    keeps the highest clear-pixel fraction."""
    best: dict[str, Any] | None = None
    for scene in scenes:
        result = await _analyse_scene(adapter, scene, aoi, index_name)
        if best is None or result["clear_fraction"] > best["clear_fraction"]:
            best = result
    assert best is not None  # callers only pass a non-empty list
    return best


async def _analyse_aoi(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Compute one index over an arbitrary AOI for its most recent usably-clear pass, without
    persisting anything. Only the few most-recent scenes are fetched (not the whole window) so a
    single click never fans out to dozens of reads, and the clearest of them is returned."""
    settings = get_settings()
    adapter = get_access_adapter(settings)
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
        result = await _analyse_scene(adapter, scene, aoi, index_name)
        if best is None or result["clear_fraction"] > best["clear_fraction"]:
            best = result
        if result["clear_fraction"] >= 0.6:  # clear enough; stop early to stay responsive
            break

    assert best is not None
    return best


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
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Multi-pass preview over a custom AOI (AOI Studio). `mode="dates"` resolves each requested
    calendar date to its same-day scene (exact day only - a date with no pass that day comes back
    as `no_pass`); `mode="backfill"` sweeps the months-back window and returns every usable pass up
    to `MAX_SERIES_PASSES`. Each pass runs through the production engine and nothing is persisted
    (invariant 6). `on_progress(done, total)` fires per scene so the task can publish a job
    progress meter.

    `adapter`, `backfill_months`, and `now` are injectable so the engine is testable against the
    mock adapter with no network (CLAUDE.md 3); the task resolves them from settings."""
    _guard_aoi_size(geometry)
    get_index(index_name)  # validate up front; KeyError surfaces as a task failure
    if adapter is None or backfill_months is None:
        settings = get_settings()
        adapter = adapter or get_access_adapter(settings)
        backfill_months = settings.backfill_months if backfill_months is None else backfill_months
    assert adapter is not None and backfill_months is not None
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    now = now or datetime.now(UTC)

    if mode == "dates":
        requested = sorted({date.fromisoformat(d) for d in (dates or [])})
        if not requested:
            raise ValueError("dates mode needs at least one date")
        search_range = TimeRange(
            start=_start_of_day(requested[0]), end=_start_of_day(requested[-1]) + timedelta(days=1)
        )
        scenes = await adapter.search(aoi, search_range, max_scene_cloud_pct=_SEARCH_CLOUD_PCT)
        by_day: dict[date, list[SceneRef]] = defaultdict(list)
        for scene in scenes:
            by_day[scene.sensing_datetime.date()].append(scene)

        passes: list[dict[str, Any]] = []
        total = len(requested)
        for done, day in enumerate(requested, start=1):
            same_day = by_day.get(day)
            if same_day:
                result = await _clearest_scene_result(adapter, same_day, aoi, index_name)
                result["requested_date"] = day.isoformat()
                passes.append(result)
            else:
                passes.append({"requested_date": day.isoformat(), "status": "no_pass"})
            if on_progress:
                on_progress(done, total)

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

        passes = []
        total = len(chosen)
        for done, scene in enumerate(chosen, start=1):
            passes.append(await _analyse_scene(adapter, scene, aoi, index_name))
            if on_progress:
                on_progress(done, total)

    else:
        raise ValueError(f"unknown AOI series mode {mode!r}")

    return {
        "status": "ok",
        "index": index_name,
        "mode": mode,
        "requested": len(passes),
        "resolved": sum(1 for p in passes if p.get("status") == "ok"),
        "passes": passes,
    }


@celery.task(name="analysis.analyse_aoi")
def analyse_aoi_task(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Ad-hoc preview analysis over a custom AOI (the workspace "analyse this area" action): the
    most recent usable pass's index stats, computed through the same engine as stored analyses but
    never persisted. No field is created, so it cannot collide with gateway-owned identity
    (invariant 6)."""
    return asyncio.run(_analyse_aoi(geometry, index_name))


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
    `{done, total}` progress meter as each pass lands. Never persists (invariant 6)."""

    def on_progress(done: int, total: int) -> None:
        self.update_state(state="PROGRESS", meta={"done": done, "total": total})

    return asyncio.run(
        _analyse_aoi_series(geometry, index_name, mode, dates, months, on_progress=on_progress)
    )
