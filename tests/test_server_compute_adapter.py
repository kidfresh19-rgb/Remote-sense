"""Logic tests for the server_compute adapter with a fake Process client + decoder (zero network,
no geo extra). Verifies per-AOI SCL + dataMask masking, the reflectance NormalizedResult, the
server-side preview PNG path, metadata parsing, and the Process API request shaping."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from rs_core.config import Settings
from rs_imagery.adapters.cdse_stac import StacItem
from rs_imagery.adapters.server_compute import DecodedGrid, ServerComputeAdapter
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
    quant = "<BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>"
    return f"<L2A>{quant}{offsets}</L2A>".encode()


def _item(scene_id: str = "S2_TEST") -> StacItem:
    return StacItem(
        scene_id=scene_id,
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        geometry=_AOI.geometry,
        crs="EPSG:4326",
        cloud_cover=12.0,
        assets={"product_metadata": {"href": "https://catalogue.example/S2_TEST/MTD_MSIL2A.xml"}},
    )


class _FakeStac:
    def __init__(self, items: list[StacItem]) -> None:
        self._items = items

    async def search_items(self, aoi, time_range, *, max_scene_cloud_pct=None):  # noqa: ANN001
        return self._items


class _FakeProcess:
    """Records each render call and returns canned bytes (the decoder is also faked)."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict, str]] = []

    async def render(self, body: dict, *, accept: str) -> bytes:
        self.calls.append((body, accept))
        return b"\x89PNG-canned" if accept == "image/png" else b"II*\x00-canned-tiff"


class _FakeDecoder:
    """Returns reflectance + SCL + dataMask arrays mapped onto the requested band order."""

    def __init__(self) -> None:
        self.band_order: list[str] | None = None
        self.values = {
            "B04": np.array([[0.05, 0.05], [0.05, 0.05]]),
            "B08": np.array([[0.20, 0.20], [0.20, 0.20]]),
            "SCL": np.array([[4, 9], [4, 4]], dtype="float64"),  # 9 == cloud (not clear)
            "dataMask": np.array([[1, 1], [0, 1]], dtype="float64"),  # bottom-left outside the AOI
        }

    def decode(self, data: bytes, *, band_order) -> DecodedGrid:  # noqa: ANN001
        self.band_order = list(band_order)
        bands = {name: self.values[name] for name in band_order}
        return DecodedGrid(bands=bands, transform=_TRANSFORM, crs=_CRS)


def _adapter(process: _FakeProcess, decoder: _FakeDecoder) -> ServerComputeAdapter:
    return ServerComputeAdapter(
        Settings(),
        stac_client=_FakeStac([_item()]),
        process_client=process,
        decoder=decoder,
        metadata_reader=lambda href: _mtd(),
    )


def _ref() -> SceneRef:
    return SceneRef(
        scene_id="S2_TEST",
        provider="cdse",
        sensing_datetime=datetime(2023, 6, 15, 8, 0, tzinfo=UTC),
        footprint=_AOI.geometry,
    )


async def test_fetch_masks_per_aoi_scl_and_datamask():
    process, decoder = _FakeProcess(), _FakeDecoder()
    adapter = _adapter(process, decoder)
    await adapter.search(_AOI, _RANGE)
    result = await adapter.fetch(_ref(), _AOI, bands=["B04", "B08"], resolution_m=10.0)

    b04 = result.data.bands["B04"]
    assert b04[0, 0] == pytest.approx(0.05)  # clear, in-AOI
    assert np.isnan(b04[0, 1])  # SCL cloud
    assert np.isnan(b04[1, 0])  # dataMask 0 (outside the AOI)
    assert result.clear_fraction == pytest.approx(2 / 3)
    assert result.provenance.processing_mode is ProcessingMode.SERVER_COMPUTE
    assert "SCL" not in result.data.bands and "dataMask" not in result.data.bands
    # The decoder was asked for the reflectance bands plus SCL and the dataMask, in order.
    assert decoder.band_order == ["B04", "B08", "SCL", "dataMask"]
    assert process.calls[0][1] == "image/tiff"
    assert "dataMask" in process.calls[0][0]["evalscript"]


async def test_fetch_populates_cloud_mask_at_parity_with_windowed_cog():
    """The cloud-honesty overlay mask (backlog 0046) is computed identically to windowed_cog, so the
    two real adapters cannot drift: True only for in-field pixels the SCL clear mask dropped."""
    process, decoder = _FakeProcess(), _FakeDecoder()
    adapter = _adapter(process, decoder)
    await adapter.search(_AOI, _RANGE)
    result = await adapter.fetch(_ref(), _AOI, bands=["B04", "B08"], resolution_m=10.0)

    assert result.cloud_mask is not None
    # scl [[4,9],[4,4]] & dataMask [[1,1],[0,1]] -> only the cloudy in-AOI pixel [0,1] is masked.
    assert np.array_equal(result.cloud_mask, np.array([[False, True], [False, False]]))


async def test_preview_returns_server_rendered_png_with_locked_range():
    process, decoder = _FakeProcess(), _FakeDecoder()
    adapter = _adapter(process, decoder)
    tile = await adapter.preview(_AOI, "ndvi", _ref())
    assert tile.png == b"\x89PNG-canned"
    assert tile.index == "ndvi"
    assert (tile.vmin, tile.vmax) == (-0.2, 0.9)  # the locked NDVI display range
    assert tile.colormap == "RdYlGn"
    assert process.calls[0][1] == "image/png"


async def test_preview_unknown_index_raises():
    adapter = _adapter(_FakeProcess(), _FakeDecoder())
    with pytest.raises(KeyError):
        await adapter.preview(_AOI, "bogus", _ref())


async def test_metadata_parses_via_reader():
    adapter = _adapter(_FakeProcess(), _FakeDecoder())
    await adapter.search(_AOI, _RANGE)
    meta = await adapter.metadata("S2_TEST")
    assert meta.quantification_value == 10000.0
    assert meta.boa_add_offset["B08"] == -1000.0


async def test_fetch_without_prior_search_raises_lookup():
    adapter = _adapter(_FakeProcess(), _FakeDecoder())
    with pytest.raises(LookupError):
        await adapter.fetch(_ref(), _AOI, bands=["B04", "B08"], resolution_m=10.0)
