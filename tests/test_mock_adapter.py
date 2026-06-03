"""Phase 0 contract tests: the mock adapter honours the AccessPort shape with zero
network and zero DB. These guard the seam every downstream phase builds against."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from rs_core.config import ImageryAdapter, Settings
from rs_imagery import (
    AOI,
    NormalizedResult,
    PreviewTile,
    ProcessingMode,
    SceneRef,
    TimeRange,
    get_access_adapter,
)

_AOI = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [
                [31.0, -17.8],
                [31.01, -17.8],
                [31.01, -17.81],
                [31.0, -17.81],
                [31.0, -17.8],
            ]
        ],
    }
)
_RANGE = TimeRange(
    start=datetime(2024, 11, 1, tzinfo=UTC),
    end=datetime(2024, 12, 1, tzinfo=UTC),
)


def _adapter():
    return get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))


async def test_search_returns_scene_refs():
    scenes = await _adapter().search(_AOI, _RANGE)
    assert scenes, "search should return scenes within the range"
    assert all(isinstance(s, SceneRef) for s in scenes)
    assert all(s.sensing_datetime.tzinfo is not None for s in scenes)


async def test_search_cloud_filter():
    scenes = await _adapter().search(_AOI, _RANGE, max_scene_cloud_pct=0.0)
    assert all((s.scene_cloud_pct or 0) <= 0.0 for s in scenes)


async def test_search_is_deterministic():
    a = await _adapter().search(_AOI, _RANGE)
    b = await _adapter().search(_AOI, _RANGE)
    assert [s.scene_id for s in a] == [s.scene_id for s in b]


async def test_metadata_carries_offset():
    adapter = _adapter()
    scenes = await adapter.search(_AOI, _RANGE)
    meta = await adapter.metadata(scenes[0].scene_id)
    # The Baseline 04.00 offset must be present and non-zero, never hard-coded downstream.
    assert meta.quantification_value == 10000.0
    assert all(v == -1000.0 for v in meta.boa_add_offset.values())


async def test_fetch_returns_normalized_shape():
    adapter = _adapter()
    scenes = await adapter.search(_AOI, _RANGE)
    result = await adapter.fetch(scenes[0], _AOI, bands=["B04", "B08"])
    assert isinstance(result, NormalizedResult)
    assert set(result.data.bands) == {"B04", "B08"}
    assert 0.0 <= result.clear_fraction <= 1.0
    assert result.provenance.processing_mode is ProcessingMode.MOCK
    # NoData is np.nan, never 0; NIR should exceed Red on average (vegetation signal).
    nir, red = result.data.bands["B08"], result.data.bands["B04"]
    assert np.nanmean(nir) > np.nanmean(red)


async def test_fetch_is_deterministic_per_scene():
    adapter = _adapter()
    scenes = await adapter.search(_AOI, _RANGE)
    r1 = await adapter.fetch(scenes[0], _AOI, bands=["B08"])
    r2 = await adapter.fetch(scenes[0], _AOI, bands=["B08"])
    np.testing.assert_array_equal(
        np.nan_to_num(r1.data.bands["B08"]), np.nan_to_num(r2.data.bands["B08"])
    )


async def test_preview_returns_png_tile():
    adapter = _adapter()
    scenes = await adapter.search(_AOI, _RANGE)
    tile = await adapter.preview(_AOI, "ndvi", scenes[0])
    assert isinstance(tile, PreviewTile)
    assert tile.png.startswith(b"\x89PNG")
    assert tile.vmin < tile.vmax


def test_aoi_rejects_non_polygon():
    with pytest.raises(ValueError):
        AOI(geometry={"type": "Point", "coordinates": [31.0, -17.8]})


def test_real_adapters_are_constructible():
    # The real CDSE adapters (ADR 0002 windowed_cog, ADR 0003 server_compute) construct without
    # network/credentials; they validate config lazily on first use, so the registry returns them
    # behind the config switch with nothing downstream changing.
    from rs_imagery.adapters import ServerComputeAdapter, WindowedCogAdapter

    windowed = get_access_adapter(Settings(imagery_adapter=ImageryAdapter.WINDOWED_COG))
    assert isinstance(windowed, WindowedCogAdapter)
    server = get_access_adapter(Settings(imagery_adapter=ImageryAdapter.SERVER_COMPUTE))
    assert isinstance(server, ServerComputeAdapter)
