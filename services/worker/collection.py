"""Collection orchestration (Phase 3): turn a field + time range into per-pass index results
by driving the AccessPort and the analysis engine. This is the body the Celery backfill /
forward-fill tasks call; kept as a plain async function so it is testable against the mock
adapter with no broker and no DB.

Resolution honesty (invariant 4): indices are grouped by their native resolution and each
group's bands are fetched at that resolution, so a 20 m index (NDRE/NDMI) is never computed on
an upsampled 10 m grid. Raw bands live only for the duration of the call and are never
persisted (S-1)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from rs_analysis import AnalysisOutput, analyze_index, get_index
from rs_imagery import AOI, AccessPort, TimeRange

from services.worker.locks import DEFAULT_LOCK_TTL_SECONDS, LockClient, enqueue_lock
from services.worker.planning import plan_scenes


@dataclass(frozen=True)
class ScenePassResult:
    """All index outputs for one field on one pass, plus the provenance needed to persist
    them additively (provider/scene id/processing mode travel from the adapter result)."""

    scene_id: str
    provider: str
    processing_mode: str
    sensing_datetime: datetime
    outputs: list[AnalysisOutput]


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
) -> list[ScenePassResult]:
    """Search the archive for the AOI over `time_range`, then compute the requested indices for
    every not-yet-processed scene. `already_processed` (scene ids done for this field at the
    current geometry version) makes the call idempotent and resumable: re-running skips
    finished scenes (dedup + gap fill, R-1). Works for backfill (a long range) and forward-fill
    (a short range since the last cursor) alike."""
    scenes = await adapter.search(aoi, time_range, max_scene_cloud_pct=max_scene_cloud_pct)
    by_id = {s.scene_id: s for s in scenes}
    to_process = plan_scenes([s.scene_id for s in scenes], already_processed)
    resolution_groups = _indices_by_resolution(indices)

    results: list[ScenePassResult] = []
    for scene_id in to_process:
        scene = by_id[scene_id]
        outputs: list[AnalysisOutput] = []
        provider = scene.provider
        processing_mode = "mock"
        for resolution_m, names in resolution_groups.items():
            bands = sorted({band for name in names for band in get_index(name).bands})
            fetched = await adapter.fetch(scene, aoi, bands=bands, resolution_m=float(resolution_m))
            processing_mode = fetched.provenance.processing_mode.value
            for name in names:
                outputs.append(
                    analyze_index(
                        reflectance=fetched.data.bands,
                        index_name=name,
                        resolution_m=int(fetched.data.resolution_m),
                        clear_fraction_override=fetched.clear_fraction,
                    )
                )
        results.append(
            ScenePassResult(
                scene_id=scene_id,
                provider=provider,
                processing_mode=processing_mode,
                sensing_datetime=scene.sensing_datetime,
                outputs=outputs,
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
        )
