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


# --- pass-to-pass difference layer (backlog 0045) ---


def test_diff_range_is_symmetric_about_zero() -> None:
    """The pass-diff range is centered on zero at +/- max(|vmin|, |vmax|), so decline and growth get
    equal visual weight even though most single-pass display ranges are asymmetric (NDVI -0.2..0.9).
    Pure math - no raster stack needed."""
    from services.tiler.render import _diff_range

    assert _diff_range("ndvi") == (-0.9, 0.9)  # max(0.2, 0.9)
    assert _diff_range("evi2") == (-0.8, 0.8)  # max(0.1, 0.8)
    assert _diff_range("ndmi") == (-0.5, 0.5)  # max(0.3, 0.5)
    lo, hi = _diff_range("savi")
    assert lo == -hi  # symmetric about zero by construction


def test_diff_range_rejects_non_scalar_view() -> None:
    """rgb / fcc / mask have no scalar colormap, so they cannot be diffed on a diverging ramp."""
    from services.tiler.render import _diff_range

    with pytest.raises(KeyError):
        _diff_range("rgb")


def _write_index_cog(arr: np.ndarray) -> str:
    """Write `arr` as an index COG over a fixed Harare field and return its temp path. The caller
    removes it. Shared by the diff render tests so both passes land on the identical grid."""
    from pyproj import Transformer
    from rs_analysis import write_cog

    lon, lat = 31.05, -17.83  # EPSG:32736
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32736", always_xy=True)
    east, north = to_utm.transform(lon, lat)
    size, px = 64, 10.0
    transform = (px, 0.0, east - size / 2 * px, 0.0, -px, north + size / 2 * px)
    data = write_cog(arr, transform=transform, crs="EPSG:32736")
    fd, path = tempfile.mkstemp(suffix=".tif")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def test_render_diff_tile_zero_diff_is_colormap_center() -> None:
    """A == B: an identical field on both passes, so B minus A is 0 at every clear pixel and must
    render as the diverging ramp's exact center (uint8 index 127) - the "no change" colour, not a
    decline or growth colour - everywhere the diff is defined."""
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")
    from rasterio.io import MemoryFile
    from rio_tiler.colormap import cmap as default_cmaps

    from services.tiler.render import _DIFF_COLORMAP, render_diff_tile

    lon, lat = 31.05, -17.83
    arr = np.full((64, 64), 0.6, dtype="float32")
    path_a = _write_index_cog(arr)
    path_b = _write_index_cog(arr)
    try:
        z = 14
        x, y = _xyz(lon, lat, z)
        png = render_diff_tile(path_a, path_b, index="ndvi", z=z, x=x, y=y)
    finally:
        os.remove(path_a)
        os.remove(path_b)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with MemoryFile(png) as mem, mem.open() as src:
        assert src.count == 4  # RGBA
        r, g, b, alpha = src.read()

    opaque = alpha == 255
    assert opaque.any()  # the field is present in this tile
    assert (alpha == 0).any()  # and there is transparent area around it
    center = default_cmaps.get(_DIFF_COLORMAP.lower())[127]
    # Every defined (opaque) pixel is the single center colour: min == max == the ramp center.
    assert int(r[opaque].min()) == int(r[opaque].max()) == int(center[0])
    assert int(g[opaque].min()) == int(g[opaque].max()) == int(center[1])
    assert int(b[opaque].min()) == int(b[opaque].max()) == int(center[2])


def test_render_diff_tile_signed_regions_land_on_opposite_sides() -> None:
    """A raster with a B > A region (growth) and a B < A region (decline) renders the two on
    opposite sides of the diverging ramp: growth on the green side, decline on the red side. Each
    region's expected colour is computed the exact way the render path does (rio-tiler
    linear_rescale into a float32 band, then astype uint8) from the same float32 subtraction the
    reader performs, so this is pixel-correct, not a hand-rounded guess."""
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")
    from rasterio.io import MemoryFile
    from rio_tiler.colormap import cmap as default_cmaps
    from rio_tiler.utils import linear_rescale

    from services.tiler.render import _DIFF_COLORMAP, _diff_range, render_diff_tile

    lon, lat = 31.05, -17.83
    a_val, grew, fell = np.float32(0.4), np.float32(0.75), np.float32(0.1)
    arr_a = np.full((64, 64), a_val, dtype="float32")
    arr_b = np.full((64, 64), fell, dtype="float32")
    arr_b[:, :32] = grew  # left half grew (B > A); right half declined (B < A)
    path_a = _write_index_cog(arr_a)
    path_b = _write_index_cog(arr_b)
    try:
        z = 14
        x, y = _xyz(lon, lat, z)
        png = render_diff_tile(path_a, path_b, index="ndvi", z=z, x=x, y=y)
    finally:
        os.remove(path_a)
        os.remove(path_b)

    with MemoryFile(png) as mem, mem.open() as src:
        r, g, b, alpha = src.read()

    lo, hi = _diff_range("ndvi")
    lut = default_cmaps.get(_DIFF_COLORMAP.lower())

    def _expected_rgb(diff_value: np.floating) -> tuple[int, int, int]:
        # Mirror rescale_image: linear_rescale (float64) stored into a float32 band, then uint8.
        idx = int(
            np.array([linear_rescale(diff_value, (lo, hi), (0, 255))], "float32").astype("uint8")[0]
        )
        return tuple(int(c) for c in lut[idx][:3])

    pos_rgb = _expected_rgb(grew - a_val)  # B - A > 0
    neg_rgb = _expected_rgb(fell - a_val)  # B - A < 0

    opaque = alpha == 255
    colours = set(zip(r[opaque].tolist(), g[opaque].tolist(), b[opaque].tolist(), strict=True))
    assert pos_rgb in colours  # the growth region is present, pixel-exact
    assert neg_rgb in colours  # the decline region is present, pixel-exact
    assert pos_rgb != neg_rgb  # and they are distinct: opposite signs, opposite colours
    assert pos_rgb[1] > pos_rgb[0]  # growth reads green (green channel dominant)
    assert neg_rgb[0] > neg_rgb[1]  # decline reads red (red channel dominant)
