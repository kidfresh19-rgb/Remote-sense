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


# --- cloud-honesty overlay (backlog 0046) ---


def test_hatch_overlay_alpha_only_within_masked_region() -> None:
    """Pixel-correctness of the hatch, provable with no raster stack: alpha is drawn only inside
    the masked (cloud/SCL) region, and inside it the pattern has both drawn and gap pixels, so the
    result is a semi-transparent hatch (base layer visible) rather than a solid fill."""
    from services.tiler.render import _HATCH_ALPHA, _hatch_overlay

    masked = np.zeros((16, 16), dtype=bool)
    masked[4:12, 4:12] = True
    rgb, alpha = _hatch_overlay(masked)

    assert rgb.shape == (3, 16, 16)
    assert alpha.shape == (16, 16)
    # Never any alpha outside the masked region: the hatch cannot bleed onto clear/out-of-field px.
    assert np.all(alpha[~masked] == 0)
    inside = alpha[masked]
    assert inside.max() == _HATCH_ALPHA  # stripe lines are semi-transparent, not opaque
    assert np.any(inside == 0)  # gaps between stripes keep the base layer visible (hatch, not fill)


def test_render_mask_tile_renders_semitransparent_hatch() -> None:
    """End to end: a boolean mask COG renders as an RGBA tile whose alpha channel carries the hatch
    only where the mask marked cloud - hatch present (max > 0) with gaps (min == 0)."""
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")
    from pyproj import Transformer
    from rasterio.io import MemoryFile
    from rs_analysis import cloud_mask_raster, write_cog

    from services.tiler.render import render_mask_tile

    lon, lat = 31.05, -17.83
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32736", always_xy=True)
    east, north = to_utm.transform(lon, lat)
    size, px = 64, 10.0
    transform = (px, 0.0, east - size / 2 * px, 0.0, -px, north + size / 2 * px)
    mask = np.zeros((size, size), dtype=bool)
    mask[16:48, 16:48] = True  # a cloudy block in the middle of the field
    data = write_cog(cloud_mask_raster(mask), transform=transform, crs="EPSG:32736")

    fd, path = tempfile.mkstemp(suffix=".tif")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        z = 14
        x, y = _xyz(lon, lat, z)
        png = render_mask_tile(path, z=z, x=x, y=y)
    finally:
        os.remove(path)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with MemoryFile(png) as mem, mem.open() as src:
        assert src.count == 4  # RGBA
        alpha = src.read(4)
    assert alpha.max() > 0  # the masked block is hatched
    assert alpha.min() == 0  # transparent between stripes and outside the mask


def test_render_mask_tile_clear_pass_is_fully_transparent() -> None:
    """A pass with no cloud (an all-NoData mask COG) renders a fully transparent tile: the overlay
    shows nothing rather than hatching the whole field."""
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")
    from pyproj import Transformer
    from rasterio.io import MemoryFile
    from rs_analysis import cloud_mask_raster, write_cog

    from services.tiler.render import render_mask_tile

    lon, lat = 31.05, -17.83
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32736", always_xy=True)
    east, north = to_utm.transform(lon, lat)
    size, px = 64, 10.0
    transform = (px, 0.0, east - size / 2 * px, 0.0, -px, north + size / 2 * px)
    data = write_cog(
        cloud_mask_raster(np.zeros((size, size), dtype=bool)), transform=transform, crs="EPSG:32736"
    )

    fd, path = tempfile.mkstemp(suffix=".tif")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        z = 14
        x, y = _xyz(lon, lat, z)
        png = render_mask_tile(path, z=z, x=x, y=y)
    finally:
        os.remove(path)

    with MemoryFile(png) as mem, mem.open() as src:
        alpha = src.read(4)
    assert alpha.max() == 0  # nothing hatched for a clear pass
