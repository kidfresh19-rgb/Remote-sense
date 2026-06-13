"""End-to-end in-container render (L5): write an index COG, then render an XYZ tile from it via
rio-tiler. Skips without the raster stack (the geo extra); runs in CI / the container."""

from __future__ import annotations

import math
import os
import tempfile

import numpy as np
import pytest


def _xyz(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def test_render_tile_over_written_cog() -> None:
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")
    from pyproj import Transformer
    from rs_analysis import write_cog

    from services.tiler.render import render_tile

    lon, lat = 31.05, -17.83  # near Harare, EPSG:32736
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32736", always_xy=True)
    east, north = to_utm.transform(lon, lat)
    size, px = 64, 10.0
    transform = (px, 0.0, east - size / 2 * px, 0.0, -px, north + size / 2 * px)
    arr = np.full((size, size), 0.6, dtype="float32")
    data = write_cog(arr, transform=transform, crs="EPSG:32736")

    fd, path = tempfile.mkstemp(suffix=".tif")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        z = 14
        x, y = _xyz(lon, lat, z)
        png = render_tile(path, index="ndvi", z=z, x=x, y=y)
    finally:
        os.remove(path)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature


def test_render_tile_composite_over_written_cog() -> None:
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")
    from pyproj import Transformer
    from rs_analysis import write_cog

    from services.tiler.render import render_tile

    lon, lat = 31.05, -17.83
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32736", always_xy=True)
    east, north = to_utm.transform(lon, lat)
    size, px = 64, 10.0
    transform = (px, 0.0, east - size / 2 * px, 0.0, -px, north + size / 2 * px)
    # A 3-band false-color stack: bright NIR over moderate red/green, the vegetation signature.
    arr = np.stack(
        [
            np.full((size, size), 0.4, dtype="float32"),
            np.full((size, size), 0.1, dtype="float32"),
            np.full((size, size), 0.08, dtype="float32"),
        ],
        axis=0,
    )
    data = write_cog(arr, transform=transform, crs="EPSG:32736")

    fd, path = tempfile.mkstemp(suffix=".tif")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        z = 14
        x, y = _xyz(lon, lat, z)
        png = render_tile(path, index="fcc", z=z, x=x, y=y)
    finally:
        os.remove(path)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
