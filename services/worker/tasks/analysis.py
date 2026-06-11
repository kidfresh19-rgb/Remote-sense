"""Ad-hoc AOI preview analysis: the workspace "analyse this area" action, computed through the
same engine as stored analyses but never persisted. No field is created, so it cannot collide
with gateway-owned identity (invariant 6)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from rs_analysis import analyze_index, get_index
from rs_core import get_settings
from rs_imagery import AOI, TimeRange, get_access_adapter

from services.worker.celery_app import celery


async def _analyse_aoi(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Compute one index over an arbitrary AOI for its most recent usably-clear pass, without
    persisting anything. Only the few most-recent scenes are fetched (not the whole window) so a
    single click never fans out to dozens of reads, and the clearest of them is returned."""
    settings = get_settings()
    adapter = get_access_adapter(settings)
    aoi = AOI(geometry=geometry, crs="EPSG:4326")
    spec = get_index(index_name)
    now = datetime.now(UTC)
    scenes = await adapter.search(
        aoi, TimeRange(start=now - timedelta(days=90), end=now), max_scene_cloud_pct=70.0
    )
    if not scenes:
        return {"status": "no_scenes"}

    bands = sorted(spec.bands)
    best_scene = None
    best_out = None
    for scene in sorted(scenes, key=lambda s: s.sensing_datetime, reverse=True)[:3]:
        fetched = await adapter.fetch(
            scene, aoi, bands=bands, resolution_m=float(spec.resolution_m)
        )
        out = analyze_index(
            reflectance=fetched.data.bands,
            index_name=index_name,
            resolution_m=int(fetched.data.resolution_m),
            clear_fraction_override=fetched.clear_fraction,
        )
        if best_out is None or out.clear_fraction > best_out.clear_fraction:
            best_scene, best_out = scene, out
        if out.clear_fraction >= 0.6:  # clear enough; stop early to stay responsive
            break

    assert best_scene is not None and best_out is not None
    s = best_out.stats
    return {
        "status": "ok",
        "index": best_out.index_name,
        "pass_date": best_scene.sensing_datetime.date().isoformat(),
        "scene_id": best_scene.scene_id,
        "mean": s.mean,
        "min": s.min,
        "max": s.max,
        "p10": s.p10,
        "p90": s.p90,
        "clear_fraction": best_out.clear_fraction,
        "confidence": best_out.confidence,
        "resolution_m": best_out.resolution_m,
        "pixels": s.count,
    }


@celery.task(name="analysis.analyse_aoi")
def analyse_aoi_task(geometry: dict[str, object], index_name: str) -> dict[str, object]:
    """Ad-hoc preview analysis over a custom AOI (the workspace "analyse this area" action): the
    most recent usable pass's index stats, computed through the same engine as stored analyses but
    never persisted. No field is created, so it cannot collide with gateway-owned identity
    (invariant 6)."""
    return asyncio.run(_analyse_aoi(geometry, index_name))
