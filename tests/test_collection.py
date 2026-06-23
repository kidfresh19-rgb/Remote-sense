"""Collection-orchestration tests (Phase 3) against the mock adapter: search -> per-pass index
computation, dedup/resume via already_processed, and resolution honesty across the index suite.
No broker, no DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
from rs_core.config import ImageryAdapter, Settings
from rs_imagery import AOI, TimeRange, get_access_adapter
from rs_imagery.adapters.mock import MockAdapter

from services.worker.collection import collect_field, collect_field_locked
from services.worker.tasks.collection import _build_collection_adapter

_AOI = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[31.0, -17.8], [31.01, -17.8], [31.01, -17.81], [31.0, -17.81], [31.0, -17.8]]
        ],
    }
)
_RANGE = TimeRange(
    start=datetime(2024, 10, 1, tzinfo=UTC),
    end=datetime(2024, 12, 1, tzinfo=UTC),
)
_INDICES = ["ndvi", "evi2", "savi", "ndre", "ndmi"]


def _adapter():
    return get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))


async def test_collect_field_returns_all_indices_per_scene() -> None:
    results = await collect_field(adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=_INDICES)
    assert results, "mock search should yield scenes in this range"
    for r in results:
        names = {o.index_name for o in r.outputs}
        assert names == set(_INDICES)
        assert all(0.0 <= o.clear_fraction <= 1.0 for o in r.outputs)


async def test_collect_field_rejects_empty_indices() -> None:
    # An empty index list would search + skip every scene and store nothing; fail fast instead.
    with pytest.raises(ValueError, match="at least one index"):
        await collect_field(adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=[])


async def test_collect_field_honours_native_resolution() -> None:
    results = await collect_field(adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=_INDICES)
    out_by_name = {o.index_name: o for o in results[0].outputs}
    assert out_by_name["ndvi"].resolution_m == 10
    assert out_by_name["ndre"].resolution_m == 20  # B05 -> 20 m, not upsampled to 10 m
    assert out_by_name["ndmi"].resolution_m == 20


async def test_collect_field_skips_already_processed() -> None:
    adapter = _adapter()
    first = await collect_field(adapter=adapter, aoi=_AOI, time_range=_RANGE, indices=["ndvi"])
    done = frozenset(r.scene_id for r in first)

    # Re-running with every discovered scene marked done yields nothing (idempotent/resumable).
    second = await collect_field(
        adapter=adapter,
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
        already_processed=done,
    )
    assert second == []


async def test_collect_field_partial_resume() -> None:
    adapter = _adapter()
    first = await collect_field(adapter=adapter, aoi=_AOI, time_range=_RANGE, indices=["ndvi"])
    skip_one = frozenset(list(r.scene_id for r in first)[:1])
    remaining = await collect_field(
        adapter=adapter,
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
        already_processed=skip_one,
    )
    assert len(remaining) == len(first) - 1
    assert skip_one.isdisjoint({r.scene_id for r in remaining})


class _CountingMock(MockAdapter):
    """Mock adapter that counts searches, so the known-scene fast path can be proven to skip the
    redundant per-pass `adapter.search`."""

    def __init__(self) -> None:
        super().__init__()
        self.searches = 0

    async def search(self, aoi, time_range, *, max_scene_cloud_pct=None):  # noqa: ANN001
        self.searches += 1
        return await super().search(aoi, time_range, max_scene_cloud_pct=max_scene_cloud_pct)


class _ColdCacheMock(_CountingMock):
    """Mimics windowed_cog with a cold scene-item cache: fetch raises LookupError for any scene the
    adapter has not 'discovered' via a search yet, so collect_field's search fallback is exercised
    without a real adapter."""

    def __init__(self) -> None:
        super().__init__()
        self._known: set[str] = set()

    async def search(self, aoi, time_range, *, max_scene_cloud_pct=None):  # noqa: ANN001
        refs = await super().search(aoi, time_range, max_scene_cloud_pct=max_scene_cloud_pct)
        self._known.update(r.scene_id for r in refs)
        return refs

    async def fetch(self, scene_ref, aoi, bands, *, resolution_m=None):  # noqa: ANN001
        if scene_ref.scene_id not in self._known:
            raise LookupError(f"scene {scene_ref.scene_id} not in this worker's cache")
        return await super().fetch(scene_ref, aoi, bands, resolution_m=resolution_m)


async def test_collect_field_known_scene_path_skips_search() -> None:
    """Given the scene refs, collect_field computes them directly without re-searching, and carries
    the real sensing_datetime through (provenance, invariant 5)."""
    adapter = _CountingMock()
    refs = await adapter.search(_AOI, _RANGE)
    adapter.searches = 0  # the fast path must not search again

    results = await collect_field(
        adapter=adapter, aoi=_AOI, time_range=_RANGE, indices=["ndvi"], scenes=refs[:1]
    )
    assert adapter.searches == 0  # no redundant per-pass search
    assert len(results) == 1
    assert results[0].scene_id == refs[0].scene_id
    assert results[0].sensing_datetime == refs[0].sensing_datetime


async def test_collect_field_falls_back_to_search_on_cold_cache() -> None:
    """A cold scene-item cache (fetch raises LookupError) makes collect_field run the search it
    skipped, so the pass still collects - identical result, just one extra search."""
    adapter = _ColdCacheMock()
    refs = await adapter.search(_AOI, _RANGE)
    adapter._known.clear()  # simulate a different worker with nothing cached
    adapter.searches = 0

    results = await collect_field(
        adapter=adapter, aoi=_AOI, time_range=_RANGE, indices=["ndvi"], scenes=refs[:1]
    )
    assert adapter.searches == 1  # exactly one fallback search rehydrated the window
    assert len(results) == 1
    assert results[0].scene_id == refs[0].scene_id


async def test_collect_field_emits_no_rasters_by_default() -> None:
    results = await collect_field(adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=["ndvi"])
    assert results
    assert all(r.rasters == {} for r in results)


async def test_collect_field_emits_index_rasters_when_requested() -> None:
    results = await collect_field(
        adapter=_adapter(),
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi", "ndre"],
        emit_rasters=True,
    )
    assert results
    rasters = results[0].rasters
    assert set(rasters) == {"ndvi", "ndre", "rgb", "fcc"}
    assert rasters["ndvi"].array.ndim == 2
    assert rasters["rgb"].array.ndim == 3 and rasters["rgb"].array.shape[0] == 3
    assert rasters["fcc"].array.ndim == 3 and rasters["fcc"].array.shape[0] == 3
    assert rasters["ndvi"].crs  # carries the grid CRS for the COG
    assert len(rasters["ndre"].transform) == 6


class _FakeCogStore:
    """In-memory stand-in for the COG store (put/exists/delete contract from CogStore protocol)."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, *, content_type: str = "image/tiff") -> None:
        self.store[key] = data

    def exists(self, key: str) -> bool:
        return key in self.store

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def test_collect_field_rgb_raster_is_float32() -> None:
    """rgb_raster() is used in the pipeline: the emitted array is float32, not float64."""
    results = await collect_field(
        adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=["ndvi"], emit_rasters=True
    )
    assert results
    rgb = results[0].rasters["rgb"]
    assert rgb.array.dtype == np.float32
    assert rgb.array.ndim == 3
    assert rgb.array.shape[0] == 3


async def test_collect_field_rgb_cog_key_written_to_store() -> None:
    """rgb.tif appears in the COG store under cog/v{gv}/{field_id}/{scene_id}/rgb.tif."""
    pytest.importorskip("rasterio")
    from rs_analysis import write_cog
    from rs_core.storage import cog_key as _cog_key

    results = await collect_field(
        adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=["ndvi"], emit_rasters=True
    )
    assert results

    field_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    gv = 1
    store = _FakeCogStore()

    for r in results:
        if "rgb" in r.rasters:
            raster = r.rasters["rgb"]
            key = _cog_key(field_id=field_id, scene_id=r.scene_id, index="rgb", geometry_version=gv)
            store.put(key, write_cog(raster.array, transform=raster.transform, crs=raster.crs))

    rgb_keys = [k for k in store.store if k.endswith("/rgb.tif")]
    assert rgb_keys, "rgb.tif must be present in the COG store for every collected scene"
    assert all(k.startswith(f"cog/v{gv}/") for k in rgb_keys)


class _FakeRedis:
    """In-memory stand-in for the enqueue lock's redis client (SET NX + compare-and-delete)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, token = keys_and_args[0], keys_and_args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


_KEY = "collect:field:f1:v1"


async def test_collect_field_locked_runs_and_releases_when_free() -> None:
    redis = _FakeRedis()
    results = await collect_field_locked(
        lock_client=redis,
        collection_key=_KEY,
        adapter=_adapter(),
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
    )
    assert results, "with the lock free, collection should run and yield scenes"
    assert redis.store == {}  # lock released after the run


async def test_collect_field_locked_skips_when_already_held() -> None:
    redis = _FakeRedis()
    redis.store[_KEY] = "held-by-another-worker"
    results = await collect_field_locked(
        lock_client=redis,
        collection_key=_KEY,
        adapter=_adapter(),
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
    )
    assert results is None  # R-1: another worker owns the unit, so skip rather than double-process
    assert redis.store[_KEY] == "held-by-another-worker"  # untouched


# ----------------------------------------------------------- collection adapter wiring (Phase 2a)


async def test_build_collection_adapter_attaches_scene_caches_for_windowed_cog() -> None:
    """The stored pipeline's windowed_cog adapter is wired with both cross-worker scene caches (the
    scene-metadata cache and the scene-item cache), returned alongside for close at task end. Lazy
    Redis client -> no network here."""
    from rs_imagery.adapters.windowed_cog import WindowedCogAdapter

    adapter, caches = _build_collection_adapter(
        Settings(imagery_adapter=ImageryAdapter.WINDOWED_COG)
    )
    assert isinstance(adapter, WindowedCogAdapter)
    assert adapter._scene_meta_cache is not None
    assert adapter._scene_item_cache is not None
    assert len(caches) == 2
    for cache in caches:
        await cache.aclose()  # release the lazily-built client; never opened a connection


def test_build_collection_adapter_passes_through_other_adapters() -> None:
    """A non-windowed_cog adapter (config switch, invariant 1) is returned unchanged with no caches
    to manage."""
    from rs_imagery.adapters.mock import MockAdapter

    adapter, caches = _build_collection_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))
    assert isinstance(adapter, MockAdapter)
    assert caches == []
