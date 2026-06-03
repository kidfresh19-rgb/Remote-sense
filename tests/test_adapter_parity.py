"""Adapter parity (T0.3, risk #5): windowed_cog and server_compute must agree on values for the same
scene and formula. Both feed the one engine, so given the same scene they must produce the same
reflectance and therefore the same index statistics. This is the offline, structural guard; the
live numeric check against the Copernicus Browser is the validation matrix (owner-blocked on CDSE).

windowed_cog reads DN and applies the offset locally; server_compute receives reflectance from the
Process API. Here the fakes are wired so the reflectance is identical, and the test asserts the two
fetch results and their engine outputs match."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from rs_analysis import analyze_index
from rs_core.config import Settings
from rs_imagery.adapters.cdse_stac import StacItem
from rs_imagery.adapters.server_compute import DecodedGrid, ServerComputeAdapter
from rs_imagery.adapters.windowed_cog import ReadWindow, WindowedCogAdapter
from rs_imagery.types import AOI, SceneRef, TimeRange

_T = (10.0, 0.0, 500000.0, 0.0, -10.0, 8000000.0)
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

# The same scene seen two ways: raw DN (windowed_cog reads this) and the reflectance the offset
# yields (server_compute receives this from the Process API). All pixels clear and in-AOI.
_DN = {
    "B04": np.array([[1500.0, 2000.0], [1500.0, 1700.0]]),
    "B08": np.array([[3000.0, 3500.0], [3200.0, 3000.0]]),
}
_REFL = {band: (dn - 1000.0) / 10000.0 for band, dn in _DN.items()}
_SCL = np.array([[4, 4], [4, 4]], dtype="float64")


def _mtd() -> bytes:
    offsets = "".join(f'<BOA_ADD_OFFSET band_id="{i}">-1000</BOA_ADD_OFFSET>' for i in range(13))
    quant = "<BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>"
    return f"<L2A>{quant}{offsets}</L2A>".encode()


def _item() -> StacItem:
    return StacItem(
        scene_id="S2_PARITY",
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        geometry=_AOI.geometry,
        crs="EPSG:4326",
        cloud_cover=5.0,
        assets={
            "B04": {"href": "s3://eodata/S2_PARITY/B04_10m.jp2"},
            "B08": {"href": "s3://eodata/S2_PARITY/B08_10m.jp2"},
            "SCL": {"href": "s3://eodata/S2_PARITY/SCL_20m.jp2"},
            "product_metadata": {"href": "https://catalogue.example/S2_PARITY/MTD_MSIL2A.xml"},
        },
    )


class _FakeStac:
    async def search_items(self, aoi, time_range, *, max_scene_cloud_pct=None):  # noqa: ANN001
        return [_item()]


class _WinSource:
    def read_window(self, href, *, aoi, resolution_m, resampling="bilinear"):  # noqa: ANN001
        arr = _SCL if "SCL" in href.upper() else next(a for b, a in _DN.items() if b in href)
        return ReadWindow(array=arr.astype("float64"), transform=_T, crs=_CRS)

    def aoi_mask(self, aoi, *, like):  # noqa: ANN001
        return np.ones(like.array.shape, dtype=bool)

    def read_bytes(self, href):  # noqa: ANN001
        return _mtd()


class _FakeProcess:
    async def render(self, body, *, accept):  # noqa: ANN001
        return b"II*\x00-canned-tiff"


class _Decoder:
    def decode(self, data, *, band_order):  # noqa: ANN001
        vals = {**_REFL, "SCL": _SCL, "dataMask": np.ones((2, 2), dtype="float64")}
        return DecodedGrid(bands={n: vals[n] for n in band_order}, transform=_T, crs=_CRS)


def _ref() -> SceneRef:
    return SceneRef(
        scene_id="S2_PARITY",
        provider="cdse",
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        footprint=_AOI.geometry,
    )


async def test_windowed_cog_and_server_compute_agree_on_ndvi():
    windowed = WindowedCogAdapter(Settings(), stac_client=_FakeStac(), window_source=_WinSource())
    server = ServerComputeAdapter(
        Settings(),
        stac_client=_FakeStac(),
        process_client=_FakeProcess(),
        decoder=_Decoder(),
        metadata_reader=lambda href: _mtd(),
    )
    await windowed.search(_AOI, _RANGE)
    await server.search(_AOI, _RANGE)

    w = await windowed.fetch(_ref(), _AOI, bands=["B04", "B08"], resolution_m=10.0)
    s = await server.fetch(_ref(), _AOI, bands=["B04", "B08"], resolution_m=10.0)

    # The reflectance the two adapters hand the engine is identical for the same scene.
    np.testing.assert_allclose(w.data.bands["B04"], s.data.bands["B04"])
    np.testing.assert_allclose(w.data.bands["B08"], s.data.bands["B08"])

    ow = analyze_index(
        reflectance=w.data.bands,
        index_name="ndvi",
        resolution_m=10,
        clear_fraction_override=w.clear_fraction,
    )
    os_ = analyze_index(
        reflectance=s.data.bands,
        index_name="ndvi",
        resolution_m=10,
        clear_fraction_override=s.clear_fraction,
    )
    # Same formula + same reflectance => identical statistics (no dual truth, risk #5).
    assert ow.stats.mean == pytest.approx(os_.stats.mean)
    assert ow.stats.min == pytest.approx(os_.stats.min)
    assert ow.stats.max == pytest.approx(os_.stats.max)
    assert ow.stats.p10 == pytest.approx(os_.stats.p10)
    assert ow.stats.p90 == pytest.approx(os_.stats.p90)
