"""Logic tests for the windowed_cog adapter with a fake WindowSource (zero network, no geo extra).

Verifies the parts that carry correctness risk: the per-scene reflectance offset is applied via the
one canonical converter, per-AOI SCL masking sets out-of-field and cloudy pixels to NaN (invariant
3), the clear fraction is the SCL clear fraction, SCL is read with nearest resampling, and the
provenance is the windowed_cog mode. The real rasterio reads live behind the seam, in-container."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from rs_core.cache import RedisJsonCache
from rs_core.config import Settings
from rs_imagery.adapters.cdse_stac import StacItem
from rs_imagery.adapters.windowed_cog import (
    RasterioWindowSource,
    ReadWindow,
    WindowedCogAdapter,
)
from rs_imagery.types import AOI, ProcessingMode, SceneRef, TimeRange

_TRANSFORM = (10.0, 0.0, 500000.0, 0.0, -10.0, 8000000.0)
_CRS = "EPSG:32735"
_AOI = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[30.0, -17.9], [30.1, -17.9], [30.1, -18.0], [30.0, -18.0], [30.0, -17.9]]
        ],
    }
)
_RANGE = TimeRange(start=datetime(2023, 1, 1, tzinfo=UTC), end=datetime(2023, 12, 1, tzinfo=UTC))


def _mtd() -> bytes:
    offsets = "".join(f'<BOA_ADD_OFFSET band_id="{i}">-1000</BOA_ADD_OFFSET>' for i in range(13))
    return (
        f"<L2A><BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>{offsets}</L2A>"
    ).encode()


def _item(scene_id: str = "S2_TEST") -> StacItem:
    return StacItem(
        scene_id=scene_id,
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        geometry=_AOI.geometry,
        crs="EPSG:4326",
        cloud_cover=12.0,
        assets={
            "B04": {"href": "s3://eodata/S2_TEST/B04_10m.jp2"},
            "B08": {"href": "s3://eodata/S2_TEST/B08_10m.jp2"},
            "SCL": {"href": "s3://eodata/S2_TEST/SCL_20m.jp2"},
            "product_metadata": {"href": "s3://eodata/S2_TEST/MTD_MSIL2A.xml"},
        },
    )


class _FakeStac:
    def __init__(self, items: list[StacItem]) -> None:
        self._items = items
        self.searches = 0

    async def search_items(self, aoi, time_range, *, max_scene_cloud_pct=None):  # noqa: ANN001
        self.searches += 1
        return self._items


class _SharedFakeRedis:
    def __init__(self, store: dict[str, str], *, fail: bool = False) -> None:
        self._store = store
        self._fail = fail

    async def get(self, name: str) -> str | None:
        if self._fail:
            raise ConnectionError("redis down")
        return self._store.get(name)

    async def set(self, name: str, value: str, *, ex: int | None = None) -> None:
        if self._fail:
            raise ConnectionError("redis down")
        self._store[name] = value


def _search_cache(store: dict[str, str], *, fail: bool = False) -> RedisJsonCache:
    return RedisJsonCache(_SharedFakeRedis(store, fail=fail), namespace="aoi:search")


class _FakeSource:
    """Returns fixed DN/SCL arrays per band and records each read so resampling can be asserted."""

    def __init__(self) -> None:
        self.dn = {
            "B04": np.array([[1500.0, 1500.0], [0.0, 1500.0]]),  # 0 == NoData
            "B08": np.array([[3000.0, 3000.0], [3000.0, 3000.0]]),
        }
        self.scl = np.array([[4, 9], [4, 4]])  # 4 vegetation (clear), 9 cloud (not clear)
        self.inside = np.array([[True, True], [False, True]])  # bottom-left outside the field
        self.reads: list[tuple[str, float, str]] = []

    def read_window(self, href, *, aoi, resolution_m, resampling="bilinear"):  # noqa: ANN001
        self.reads.append((href, resolution_m, resampling))
        if "SCL" in href.upper():
            array = self.scl.astype("float64")
        else:
            array = next(a for band, a in self.dn.items() if band in href).astype("float64")
        return ReadWindow(array=array, transform=_TRANSFORM, crs=_CRS)

    def aoi_mask(self, aoi, *, like):  # noqa: ANN001
        return self.inside

    def read_bytes(self, href):  # noqa: ANN001
        return _mtd()


def _adapter(
    source: _FakeSource,
    stac_client: _FakeStac | None = None,
    search_cache: RedisJsonCache | None = None,
) -> WindowedCogAdapter:
    client = stac_client or _FakeStac([_item()])
    return WindowedCogAdapter(
        Settings(),
        stac_client=client,  # type: ignore[arg-type]
        window_source=source,
        search_cache=search_cache,
    )


async def test_search_caches_items_and_returns_scene_refs():
    adapter = _adapter(_FakeSource())
    scenes = await adapter.search(_AOI, _RANGE)
    assert [s.scene_id for s in scenes] == ["S2_TEST"]
    assert scenes[0].provider == "cdse"


async def test_fetch_applies_offset_and_masks_per_aoi_scl():
    source = _FakeSource()
    adapter = _adapter(source)
    await adapter.search(_AOI, _RANGE)
    ref = SceneRef(
        scene_id="S2_TEST",
        provider="cdse",
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        footprint=_AOI.geometry,
    )
    result = await adapter.fetch(ref, _AOI, bands=["B04", "B08"], resolution_m=10.0)

    b04 = result.data.bands["B04"]
    b08 = result.data.bands["B08"]
    # Reflectance: (1500 - 1000) / 10000 = 0.05 on a clear, in-field pixel.
    assert b04[0, 0] == pytest.approx(0.05)
    assert b08[1, 1] == pytest.approx(0.20)
    # Cloud pixel (SCL 9) masked to NaN; out-of-field pixel masked to NaN.
    assert np.isnan(b04[0, 1])  # cloudy
    assert np.isnan(b04[1, 0])  # outside the AOI polygon
    # Per-AOI SCL clear fraction: 2 clear of 3 in-field pixels.
    assert result.clear_fraction == pytest.approx(2 / 3)
    assert result.provenance.processing_mode is ProcessingMode.WINDOWED_COG
    assert result.data.resolution_m == 10.0
    assert result.data.crs == _CRS


async def test_fetch_reads_scl_with_nearest_resampling():
    source = _FakeSource()
    adapter = _adapter(source)
    await adapter.search(_AOI, _RANGE)
    ref = SceneRef(
        scene_id="S2_TEST",
        provider="cdse",
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        footprint=_AOI.geometry,
    )
    await adapter.fetch(ref, _AOI, bands=["B04", "B08"], resolution_m=10.0)
    by_resampling = {("SCL" in href.upper()): method for href, _, method in source.reads}
    assert by_resampling[True] == "nearest"  # SCL is a class label, never interpolated
    assert by_resampling[False] == "bilinear"  # reflectance bands


async def test_metadata_parses_offset_and_quantification():
    adapter = _adapter(_FakeSource())
    await adapter.search(_AOI, _RANGE)
    meta = await adapter.metadata("S2_TEST")
    assert meta.quantification_value == 10000.0
    assert meta.boa_add_offset["B04"] == -1000.0


async def test_fetch_without_prior_search_raises_lookup():
    adapter = _adapter(_FakeSource())
    ref = SceneRef(
        scene_id="UNSEEN",
        provider="cdse",
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        footprint=_AOI.geometry,
    )
    with pytest.raises(LookupError):
        await adapter.fetch(ref, _AOI, bands=["B04", "B08"], resolution_m=10.0)


def test_rasterio_source_read_bytes_reads_s3_via_boto3(monkeypatch):
    """The metadata XML is an s3:// object read with boto3 against the CDSE eodata endpoint; the
    href's bucket/key are parsed and the call is signed for the configured endpoint. Zero network:
    boto3.client is faked."""
    pytest.importorskip("boto3")
    captured: dict[str, object] = {}

    class _Body:
        def read(self) -> bytes:
            return b"<L2A/>"

    class _Client:
        def get_object(self, *, Bucket: str, Key: str):  # noqa: N803
            captured["bucket"] = Bucket
            captured["key"] = Key
            return {"Body": _Body()}

    def _fake_client(service: str, **kwargs: object) -> _Client:
        captured["service"] = service
        captured["endpoint"] = kwargs.get("endpoint_url")
        return _Client()

    monkeypatch.setattr("boto3.client", _fake_client)
    source = RasterioWindowSource(
        Settings(cdse_s3_endpoint="eodata.example", cdse_s3_access_key="a", cdse_s3_secret_key="b")
    )
    data = source.read_bytes("s3://eodata/Sentinel-2/MSI/L2A/scene.SAFE/MTD_MSIL2A.xml")
    assert data == b"<L2A/>"
    assert captured["service"] == "s3"
    assert captured["bucket"] == "eodata"
    assert captured["key"] == "Sentinel-2/MSI/L2A/scene.SAFE/MTD_MSIL2A.xml"
    assert captured["endpoint"] == "https://eodata.example"


# --------------------------------------------------------------------------- band/metadata memo


def _rio_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        cdse_s3_endpoint="eodata.example",
        cdse_s3_access_key="a",
        cdse_s3_secret_key="b",
        cdse_rate_limit_rps=None,  # no bucket -> no Redis in these unit tests
    )


_AOI_OTHER = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[31.0, -18.9], [31.1, -18.9], [31.1, -19.0], [31.0, -19.0], [31.0, -18.9]]
        ],
    }
)


class _CountingRasterioSource(RasterioWindowSource):
    """A real RasterioWindowSource with the actual reads stubbed, so the in-process memo can be
    exercised with no network and no GDAL."""

    def __init__(self, *, delay: float = 0.0) -> None:
        super().__init__(_rio_settings())
        self.reads = 0
        self.byte_reads = 0
        self._delay = delay

    def _read_window_once(self, href, *, aoi, resolution_m, resampling="bilinear"):  # noqa: ANN001
        if self._delay:
            time.sleep(self._delay)
        self.reads += 1
        return ReadWindow(array=np.zeros((4, 4)), transform=_TRANSFORM, crs=_CRS)

    def _read_s3_bytes(self, href):  # noqa: ANN001
        self.byte_reads += 1
        return b"<xml/>"


def test_band_memo_serves_repeat_reads_once() -> None:
    src = _CountingRasterioSource()
    w1 = src.read_window("s3://eodata/x/B04_10m.jp2", aoi=_AOI, resolution_m=10.0)
    w2 = src.read_window("s3://eodata/x/B04_10m.jp2", aoi=_AOI, resolution_m=10.0)
    assert src.reads == 1  # the second read is served from the memo
    assert w1 is w2
    assert src.cache_stats() == {"hits": 1, "misses": 1}


def test_band_memo_keys_on_bbox_and_resolution() -> None:
    src = _CountingRasterioSource()
    href = "s3://eodata/x/B04_10m.jp2"
    src.read_window(href, aoi=_AOI, resolution_m=10.0)
    src.read_window(href, aoi=_AOI_OTHER, resolution_m=10.0)  # different bbox -> real read
    src.read_window(href, aoi=_AOI, resolution_m=20.0)  # different resolution -> real read
    assert src.reads == 3


def test_metadata_xml_is_memoized() -> None:
    src = _CountingRasterioSource()
    assert src.read_bytes("s3://eodata/x/MTD_MSIL2A.xml") == b"<xml/>"
    assert src.read_bytes("s3://eodata/x/MTD_MSIL2A.xml") == b"<xml/>"
    assert src.byte_reads == 1
    assert src.cache_stats() == {"hits": 1, "misses": 1}


def test_band_memo_collapses_concurrent_identical_reads() -> None:
    src = _CountingRasterioSource(delay=0.02)  # widen the in-flight window so threads overlap
    n = 16
    barrier = threading.Barrier(n)

    def hit() -> None:
        barrier.wait()
        src.read_window("s3://eodata/x/B04_10m.jp2", aoi=_AOI, resolution_m=10.0)

    threads = [threading.Thread(target=hit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert src.reads == 1  # the in-flight lock collapsed all n to a single read
    assert src.cache_stats() == {"hits": n - 1, "misses": 1}


async def test_search_cache_hits_redis_and_bypasses_stac_query() -> None:
    store: dict[str, str] = {}
    stac = _FakeStac([_item()])
    cache = _search_cache(store)
    source = _FakeSource()

    # First search: cache is empty, calls STAC, populates cache
    adapter1 = _adapter(source, stac_client=stac, search_cache=cache)
    scenes1 = await adapter1.search(_AOI, _RANGE)
    assert len(scenes1) == 1
    assert scenes1[0].scene_id == "S2_TEST"
    assert stac.searches == 1

    # Second search: on a new adapter instance, hits cache, bypasses STAC
    stac2 = _FakeStac([_item()])
    adapter2 = _adapter(source, stac_client=stac2, search_cache=cache)
    scenes2 = await adapter2.search(_AOI, _RANGE)
    assert len(scenes2) == 1
    assert scenes2[0].scene_id == "S2_TEST"
    assert stac2.searches == 0  # bypassed!

    # Verify the items dictionary is repopulated so fetch works
    assert "S2_TEST" in adapter2._items


async def test_search_cache_separates_keys() -> None:
    store: dict[str, str] = {}
    stac = _FakeStac([_item()])
    cache = _search_cache(store)
    source = _FakeSource()

    adapter = _adapter(source, stac_client=stac, search_cache=cache)

    # Run a search to populate cache
    await adapter.search(_AOI, _RANGE, max_scene_cloud_pct=70.0)
    assert stac.searches == 1

    # Search with different max_scene_cloud_pct -> different key, misses cache
    await adapter.search(_AOI, _RANGE, max_scene_cloud_pct=50.0)
    assert stac.searches == 2

    # Search with different time range -> different key, misses cache
    different_range = TimeRange(start=_RANGE.start + timedelta(days=1), end=_RANGE.end)
    await adapter.search(_AOI, different_range, max_scene_cloud_pct=70.0)
    assert stac.searches == 3


async def test_search_cache_fails_open() -> None:
    stac = _FakeStac([_item()])
    # Redis client configured to fail on operations
    cache = _search_cache({}, fail=True)
    source = _FakeSource()

    adapter = _adapter(source, stac_client=stac, search_cache=cache)

    # Search succeeds by falling back to live query, no crash
    scenes = await adapter.search(_AOI, _RANGE)
    assert len(scenes) == 1
    assert scenes[0].scene_id == "S2_TEST"
    assert stac.searches == 1


async def test_multi_index_collapses_shared_reads(monkeypatch) -> None:
    from services.worker.tasks.analysis import _analyse_aoi_series_multi

    # Inject a counting RasterioWindowSource
    src = _CountingRasterioSource()
    monkeypatch.setattr(src, "_read_s3_bytes", lambda href: _mtd())
    adapter = _adapter(src)

    # Pre-populate items in the adapter by running search
    await adapter.search(_AOI, _RANGE)

    # Run NDVI and SAVI (which both fetch B04, B08, and SCL) for the same scene date
    out = await _analyse_aoi_series_multi(
        _AOI.geometry,
        ["ndvi", "savi"],
        "dates",
        ["2023-06-15"],
        None,
        adapter=adapter,
        backfill_months=18,
        now=datetime(2023, 6, 15, tzinfo=UTC),
    )

    # Assert correct structure
    assert out["status"] == "ok"
    assert "ndvi" in out["indices"]
    assert "savi" in out["indices"]
    assert out["indices"]["ndvi"]["passes"][0]["status"] == "ok"
    assert out["indices"]["savi"]["passes"][0]["status"] == "ok"

    # NDVI fetches metadata, B04, B08, SCL -> 4 misses.
    # SAVI fetches metadata, B04, B08, SCL -> 4 hits.
    # Total window reads made to the GDAL source is 3 (bands).
    assert src.reads == 3
    # Cache stats: hits = 4 (XML + B04, B08, SCL during SAVI), misses = 4 (during NDVI)
    assert src.cache_stats() == {"hits": 4, "misses": 4}


# ----------------------------------------------------------------- scene-metadata cache (Phase 2a)


def _scene_meta_cache(store: dict[str, str], *, fail: bool = False) -> RedisJsonCache:
    return RedisJsonCache(_SharedFakeRedis(store, fail=fail), namespace="cdse:scene_meta")


class _CountingMetaSource(_FakeSource):
    """A _FakeSource that counts product-XML reads, so a scene-metadata cache hit can be proven to
    skip the CDSE read entirely."""

    def __init__(self) -> None:
        super().__init__()
        self.byte_reads = 0

    def read_bytes(self, href):  # noqa: ANN001
        self.byte_reads += 1
        return _mtd()


def _meta_adapter(source: _FakeSource, cache: RedisJsonCache) -> WindowedCogAdapter:
    return WindowedCogAdapter(
        Settings(),
        stac_client=_FakeStac([_item()]),  # type: ignore[arg-type]
        window_source=source,
        scene_meta_cache=cache,
    )


async def test_metadata_cache_hit_skips_xml_read_across_tasks() -> None:
    store: dict[str, str] = {}
    cache = _scene_meta_cache(store)

    # First task: a miss reads the product XML once, then writes the parsed metadata to Redis.
    src1 = _CountingMetaSource()
    adapter1 = _meta_adapter(src1, cache)
    await adapter1.search(_AOI, _RANGE)
    meta1 = await adapter1.metadata("S2_TEST")
    assert src1.byte_reads == 1
    assert meta1.quantification_value == 10000.0
    assert meta1.boa_add_offset["B04"] == -1000.0
    assert store  # the parsed metadata is now cached for other workers

    # A second task (fresh adapter + source, shared Redis) sharing the tile hits the cache: no CDSE
    # read, identical radiometry. No prior search is even needed - metadata is scene-only.
    src2 = _CountingMetaSource()
    adapter2 = _meta_adapter(src2, cache)
    meta2 = await adapter2.metadata("S2_TEST")
    assert src2.byte_reads == 0
    assert meta2.quantification_value == meta1.quantification_value
    assert meta2.boa_add_offset == meta1.boa_add_offset
    assert meta2.crs == meta1.crs


async def test_metadata_cache_fails_open_to_live_read() -> None:
    src = _CountingMetaSource()
    adapter = _meta_adapter(src, _scene_meta_cache({}, fail=True))
    await adapter.search(_AOI, _RANGE)
    meta = await adapter.metadata("S2_TEST")  # cache.get raises -> fall open to the live XML read
    assert src.byte_reads == 1
    assert meta.quantification_value == 10000.0
