"""Index COG output (D1): the pure index-raster prep runs on the host; encoding to a GeoTIFF needs
rasterio (the geo extra), so that round-trip skips without it and runs in CI / the container."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis import index_raster


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
