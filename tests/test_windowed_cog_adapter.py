"""Logic tests for the windowed_cog adapter with a fake WindowSource (zero network, no geo extra).

Verifies the parts that carry correctness risk: the per-scene reflectance offset is applied via the
one canonical converter, per-AOI SCL masking sets out-of-field and cloudy pixels to NaN (invariant
3), the clear fraction is the SCL clear fraction, SCL is read with nearest resampling, and the
provenance is the windowed_cog mode. The real rasterio reads live behind the seam, in-container."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
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

    async def search_items(self, aoi, time_range, *, max_scene_cloud_pct=None):  # noqa: ANN001
        return self._items


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


def _adapter(source: _FakeSource) -> WindowedCogAdapter:
    return WindowedCogAdapter(Settings(), stac_client=_FakeStac([_item()]), window_source=source)


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
