"""Collection orchestration (Phase 3): turn a field + time range into per-pass index results
by driving the AccessPort and the analysis engine. This is the body the Celery backfill /
forward-fill tasks call; kept as a plain async function so it is testable against the mock
adapter with no broker and no DB.

Resolution honesty (invariant 4): indices are grouped by their native resolution and each
group's bands are fetched at that resolution, so a 20 m index (NDRE/NDMI) is never computed on
an upsampled 10 m grid. Raw bands live only for the duration of the call and are never
persisted (S-1)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from rs_analysis import AnalysisOutput, analyze_index, get_index, index_raster, rgb_raster
from rs_imagery import AOI, AccessPort, SceneMetadata, TimeRange

from services.worker.locks import DEFAULT_LOCK_TTL_SECONDS, LockClient, enqueue_lock
from services.worker.planning import plan_scenes


@dataclass(frozen=True)
class IndexRaster:
    """A computed index as a georeferenced array, carried out of collection so the I/O layer can
    write + store its COG (D1/D7) without re-fetching reflectance."""

    array: np.ndarray
    transform: tuple[float, float, float, float, float, float]
    crs: str


@dataclass(frozen=True)
class ScenePassResult:
    """All index outputs for one field on one pass, plus the provenance needed to persist
    them additively (provider/scene id/processing mode travel from the adapter result)."""

    scene_id: str
    provider: str
    processing_mode: str
    sensing_datetime: datetime
    outputs: list[AnalysisOutput]
    scene_metadata: SceneMetadata
    rasters: dict[str, IndexRaster] = field(default_factory=dict)


def _indices_by_resolution(indices: list[str]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = defaultdict(list)
    for name in indices:
        grouped[get_index(name).resolution_m].append(name)
    return dict(grouped)


async def collect_field(
    *,
    adapter: AccessPort,
    aoi: AOI,
    time_range: TimeRange,
    indices: list[str],
    already_processed: frozenset[str] = frozenset(),
    max_scene_cloud_pct: float | None = None,
    emit_rasters: bool = False,
) -> list[ScenePassResult]:
    """Search the archive for the AOI over `time_range`, then compute the requested indices for
    every not-yet-processed scene. `already_processed` (scene ids done for this field at the
    current geometry version) makes the call idempotent and resumable: re-running skips
    finished scenes (dedup + gap fill, R-1). Works for backfill (a long range) and forward-fill
    (a short range since the last cursor) alike."""
    if not indices:
        raise ValueError("collect_field needs at least one index; an empty list stores nothing")
    scenes = await adapter.search(aoi, time_range, max_scene_cloud_pct=max_scene_cloud_pct)
    by_id = {s.scene_id: s for s in scenes}
    to_process = plan_scenes([s.scene_id for s in scenes], already_processed)
    resolution_groups = _indices_by_resolution(indices)

    results: list[ScenePassResult] = []
    for scene_id in to_process:
        scene = by_id[scene_id]
        provider = scene.provider

        # Pre-compute band lists for each resolution group (pure — no I/O).
        resolution_keys = list(resolution_groups.keys())
        band_lists: list[list[str]] = []
        for res_m in resolution_keys:
            names_for_res = resolution_groups[res_m]
            bands = sorted({band for name in names_for_res for band in get_index(name).bands})
            if emit_rasters and res_m == 10:
                # Visual-composite bands: true color (B04/B03/B02) plus NIR for false color.
                bands = sorted(set(bands) | {"B08", "B04", "B03", "B02"})
            band_lists.append(bands)

        # All CDSE I/O for this scene runs concurrently: one fetch per resolution group plus
        # the metadata XML read. The RasterioWindowSource is a per-adapter lazy singleton with
        # a _ReadMemo that collapses concurrent identical href reads to one actual S3 call, so
        # the metadata XML fetch inside each adapter.fetch() and this explicit metadata() call
        # produce at most one network round-trip between them.
        fetch_results, scene_metadata = await asyncio.gather(
            asyncio.gather(
                *(
                    adapter.fetch(scene, aoi, bands=bl, resolution_m=float(rm))
                    for rm, bl in zip(resolution_keys, band_lists, strict=True)
                )
            ),
            adapter.metadata(scene_id),
        )

        outputs: list[AnalysisOutput] = []
        rasters: dict[str, IndexRaster] = {}
        processing_mode = "mock"
        fetched_bands_10m: dict[str, np.ndarray] | None = None
        transform_10m: tuple[float, float, float, float, float, float] | None = None
        crs_10m: str | None = None

        for res_m, names_for_res, fetched in zip(
            resolution_keys, resolution_groups.values(), fetch_results, strict=True
        ):
            processing_mode = fetched.provenance.processing_mode.value
            if res_m == 10:
                fetched_bands_10m = fetched.data.bands
                transform_10m = fetched.data.transform
                crs_10m = fetched.data.crs
            for name in names_for_res:
                outputs.append(
                    analyze_index(
                        reflectance=fetched.data.bands,
                        index_name=name,
                        resolution_m=int(fetched.data.resolution_m),
                        clear_fraction_override=fetched.clear_fraction,
                    )
                )
                if emit_rasters:
                    rasters[name] = IndexRaster(
                        array=index_raster(fetched.data.bands, name),
                        transform=fetched.data.transform,
                        crs=fetched.data.crs,
                    )

        # Safety fallback: only reached if all CORE_INDICES happen to be 20 m-only.
        if emit_rasters and fetched_bands_10m is None:
            fetched_10m = await adapter.fetch(
                scene, aoi, bands=["B08", "B04", "B03", "B02"], resolution_m=10.0
            )
            fetched_bands_10m = fetched_10m.data.bands
            transform_10m = fetched_10m.data.transform
            crs_10m = fetched_10m.data.crs

        if emit_rasters and fetched_bands_10m is not None:
            nir = fetched_bands_10m.get("B08")
            red = fetched_bands_10m.get("B04")
            green = fetched_bands_10m.get("B03")
            blue = fetched_bands_10m.get("B02")
            if red is not None and green is not None and blue is not None:
                assert transform_10m is not None and crs_10m is not None
                rasters["rgb"] = IndexRaster(
                    array=rgb_raster({"B04": red, "B03": green, "B02": blue}),
                    transform=transform_10m,
                    crs=crs_10m,
                )
            # False color (B08/B04/B03, NIR first): vegetation renders red.
            if nir is not None and red is not None and green is not None:
                assert transform_10m is not None and crs_10m is not None
                rasters["fcc"] = IndexRaster(
                    array=np.stack([nir, red, green], axis=0),
                    transform=transform_10m,
                    crs=crs_10m,
                )

        results.append(
            ScenePassResult(
                scene_id=scene_id,
                provider=provider,
                processing_mode=processing_mode,
                sensing_datetime=scene.sensing_datetime,
                outputs=outputs,
                scene_metadata=scene_metadata,
                rasters=rasters,
            )
        )
    return results


async def collect_field_locked(
    *,
    lock_client: LockClient,
    collection_key: str,
    adapter: AccessPort,
    aoi: AOI,
    time_range: TimeRange,
    indices: list[str],
    already_processed: frozenset[str] = frozenset(),
    max_scene_cloud_pct: float | None = None,
    emit_rasters: bool = False,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> list[ScenePassResult] | None:
    """Run `collect_field` for a work unit only while this worker holds its enqueue lock (R-1).
    Returns the per-pass results when we won the lock, or None when another worker already holds
    it - the caller simply skips, which is the dedup, not an error. `collection_key` sets the
    granularity (per field+geometry version for a backfill run, or per field/scene/version for a
    single-scene task); build it with planning.collection_key."""
    async with enqueue_lock(lock_client, collection_key, ttl_seconds=ttl_seconds) as acquired:
        if not acquired:
            return None
        return await collect_field(
            adapter=adapter,
            aoi=aoi,
            time_range=time_range,
            indices=indices,
            already_processed=already_processed,
            max_scene_cloud_pct=max_scene_cloud_pct,
            emit_rasters=emit_rasters,
        )
