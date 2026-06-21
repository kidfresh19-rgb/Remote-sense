"""Index COG output (D1): the pure index-raster prep runs on the host; encoding to a GeoTIFF needs
rasterio (the geo extra), so that round-trip skips without it and runs in CI / the container."""

from __future__ import annotations

import math

import numpy as np
import pytest
from rs_analysis import index_raster
from rs_analysis.cog import rgb_raster


def _ndvi_bands() -> dict[str, np.ndarray]:
    red = np.array([[0.1, 0.1], [0.1, 0.1]], dtype="float32")
    nir = np.array([[0.5, 0.5], [0.5, 0.5]], dtype="float32")
    return {"B04": red, "B08": nir}


def test_index_raster_computes_ndvi() -> None:
    raster = index_raster(_ndvi_bands(), "ndvi")
    assert raster.dtype == np.float32
    assert raster.shape == (2, 2)
    assert np.allclose(raster, 0.4 / 0.6, atol=1e-4)  # (0.5-0.1)/(0.5+0.1)


def test_index_raster_masks_out_of_aoi_to_nodata() -> None:
    aoi = np.array([[True, True], [False, False]])
    raster = index_raster(_ndvi_bands(), "ndvi", aoi_mask=aoi)
    assert np.isclose(raster[0, 0], 0.4 / 0.6, atol=1e-4)
    assert np.isnan(raster[1, 0]) and np.isnan(raster[1, 1])


def test_write_cog_round_trips() -> None:
    pytest.importorskip("rasterio")
    from rasterio.io import MemoryFile
    from rs_analysis import write_cog

    arr = np.linspace(-0.2, 0.9, num=64 * 64, dtype="float32").reshape(64, 64)
    transform = (10.0, 0.0, 500000.0, 0.0, -10.0, 8030000.0)  # 10 m pixels, UTM-like origin
    data = write_cog(arr, transform=transform, crs="EPSG:32735")

    assert data[:2] in (b"II", b"MM")  # TIFF magic (little/big-endian)
    with MemoryFile(data) as mem, mem.open() as src:
        assert src.count == 1
        assert (src.width, src.height) == (64, 64)
        assert src.crs.to_epsg() == 32735
        assert src.profile["tiled"] is True
        assert np.isclose(src.read(1)[0, 0], -0.2, atol=1e-3)


def test_write_cog_3d_round_trips() -> None:
    pytest.importorskip("rasterio")
    from rasterio.io import MemoryFile
    from rs_analysis import write_cog

    arr = np.linspace(-0.2, 0.9, num=3 * 64 * 64, dtype="float32").reshape(3, 64, 64)
    transform = (10.0, 0.0, 500000.0, 0.0, -10.0, 8030000.0)
    data = write_cog(arr, transform=transform, crs="EPSG:32735")

    assert data[:2] in (b"II", b"MM")
    with MemoryFile(data) as mem, mem.open() as src:
        assert src.count == 3
        assert (src.width, src.height) == (64, 64)
        assert src.crs.to_epsg() == 32735
        assert src.profile["tiled"] is True
        assert np.isclose(src.read(1)[0, 0], -0.2, atol=1e-3)


# --- rgb_raster tests ---


def _rgb_bands(h: int = 4, w: int = 4) -> dict[str, np.ndarray]:
    return {
        "B04": np.full((h, w), 0.15, dtype="float32"),
        "B03": np.full((h, w), 0.10, dtype="float32"),
        "B02": np.full((h, w), 0.05, dtype="float32"),
    }


def test_rgb_raster_shape_and_dtype() -> None:
    out = rgb_raster(_rgb_bands())
    assert out.shape == (3, 4, 4)
    assert out.dtype == np.float32


def test_rgb_raster_band_order() -> None:
    """Band 1 must be Red (B04), band 2 Green (B03), band 3 Blue (B02)."""
    bands = {
        "B04": np.full((4, 4), 0.3, dtype="float32"),
        "B03": np.full((4, 4), 0.2, dtype="float32"),
        "B02": np.full((4, 4), 0.1, dtype="float32"),
    }
    out = rgb_raster(bands)
    assert np.allclose(out[0], 0.3)
    assert np.allclose(out[1], 0.2)
    assert np.allclose(out[2], 0.1)


def test_rgb_raster_masks_outside_aoi() -> None:
    bands = _rgb_bands(2, 2)
    aoi = np.array([[True, True], [False, False]])
    out = rgb_raster(bands, aoi_mask=aoi)
    assert not np.any(np.isnan(out[:, 0, :]))
    assert np.all(np.isnan(out[:, 1, :]))


def test_rgb_raster_missing_band_raises() -> None:
    bands = {"B04": np.ones((2, 2), dtype="float32"), "B02": np.ones((2, 2), dtype="float32")}
    with pytest.raises(ValueError, match="B03"):
        rgb_raster(bands)


def test_write_cog_rgb_nodata_tagged() -> None:
    """NaN nodata survives the COG encode/decode round-trip for a 3-band RGB array."""
    pytest.importorskip("rasterio")
    from rasterio.io import MemoryFile
    from rs_analysis import write_cog

    bands = {
        "B04": np.full((8, 8), 0.15, dtype="float32"),
        "B03": np.full((8, 8), 0.10, dtype="float32"),
        "B02": np.full((8, 8), 0.05, dtype="float32"),
    }
    aoi = np.ones((8, 8), dtype=bool)
    aoi[6:, 6:] = False
    arr = rgb_raster(bands, aoi_mask=aoi)
    transform = (10.0, 0.0, 500000.0, 0.0, -10.0, 8030000.0)
    cog = write_cog(arr, transform=transform, crs="EPSG:32735")

    with MemoryFile(cog) as mem, mem.open() as src:
        assert src.count == 3
        assert math.isnan(src.nodata)
        assert np.all(np.isnan(src.read(1)[6:, 6:]))
