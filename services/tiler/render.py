"""Tile rendering for the tiler (L5). `render_params` is pure (the rescale + colormap an index is
drawn with, from the locked display range); `render_tile` reads an index COG window and colorizes
it with rio-tiler, which needs the raster stack (the `geo` extra, in-container only)."""

from __future__ import annotations

import os
from typing import cast

import numpy as np
from rs_analysis import get_colormap

# Visual composites: a fixed per-channel reflectance stretch, no colormap. The fcc red channel
# is NIR, which runs brighter over vegetation than the visible bands, so it gets a wider range.
# rgb caps at 0.3 (not 1.0): Sentinel-2 BOA land reflectance sits roughly 0.03-0.20 in visible
# bands, so 0.3 is a natural true-colour ceiling that fills the display range without blowing out
# bright surfaces. The stored RGB COG keeps raw float32 reflectance; only the preview is stretched.
_COMPOSITE_RANGES: dict[str, tuple[tuple[float, float], ...]] = {
    "rgb": ((0.0, 0.3), (0.0, 0.3), (0.0, 0.3)),
    "fcc": ((0.0, 0.45), (0.0, 0.3), (0.0, 0.3)),
}

# Cloud-honesty overlay (backlog 0046). Owner-decided 2026-07-01: cloud/SCL-masked pixels render as
# a semi-transparent diagonal hatch - not a solid fill, not full transparency - so the base layer
# (index / rgb / fcc) stays visible underneath and the region reads as "uncertain", not "hidden".
# The pattern is drawn in tile-pixel space, so its on-screen density is constant at every zoom. RGB
# is a neutral slate that reads over any base layer; alpha is partial on the stripe and zero between
# stripes, which is what makes it a hatch rather than a wash.
_HATCH_RGB: tuple[int, int, int] = (51, 65, 85)  # slate; neutral over heatmap and true/false colour
_HATCH_ALPHA: int = 140  # ~55% opacity on the stripe lines
_HATCH_PERIOD_PX: int = 8  # stripe repeat period in tile pixels
_HATCH_LINE_PX: int = 3  # drawn stripe width within each period


def _hatch_overlay(masked: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Build the (RGB, alpha) arrays for the hatch overlay from a boolean `masked` (H, W) grid,
    True where a pixel is cloud/SCL-masked. RGB is the constant hatch colour (H, W per channel);
    alpha carries the diagonal-stripe pattern gated to the masked pixels and is 0 everywhere else,
    so a masked pixel shows a semi-transparent stripe and a clear pixel stays fully transparent.
    Pure NumPy - unit-testable with no raster stack."""
    h, w = masked.shape
    yy, xx = np.mgrid[0:h, 0:w]
    stripe = ((xx + yy) % _HATCH_PERIOD_PX) < _HATCH_LINE_PX
    alpha = np.where(np.asarray(masked, dtype=bool) & stripe, _HATCH_ALPHA, 0).astype("uint8")
    rgb = np.zeros((3, h, w), dtype="uint8")
    for i, channel in enumerate(_HATCH_RGB):
        rgb[i] = channel
    return rgb, alpha


class RasterStackUnavailable(RuntimeError):
    """The raster stack (`rasterio` / `rio-tiler`, the `geo` extra) is not installed - the host
    case. The route surfaces this as 503."""


class TileUnavailable(RuntimeError):
    """No readable COG at the source, or the requested tile lies outside its coverage. The route
    surfaces this as 404."""


def render_params(index: str) -> dict[str, object]:
    """The rescale range + colormap name a tile of `index` is rendered with (matplotlib / rio-tiler
    convention), read from the locked display range. Raises KeyError for an unknown index."""
    if index in _COMPOSITE_RANGES:
        return {"colormap_name": None, "rescale": _COMPOSITE_RANGES[index]}
    colormap = get_colormap(index)
    return {"colormap_name": colormap.colormap, "rescale": (colormap.vmin, colormap.vmax)}


def render_preview(
    source: str,
    *,
    index: str,
    max_size: int = 512,
    gdal_env: dict[str, str] | None = None,
) -> bytes:
    """Full-extent JPEG preview from a stored COG using rio-tiler's decimated overview read.
    Used by the `/static/` thumbnail endpoint. Raises RasterStackUnavailable without the `geo`
    extra, TileUnavailable for a missing or unreadable COG."""
    try:
        import rasterio
        from rasterio.errors import RasterioIOError
        from rio_tiler.io import Reader
    except ImportError as exc:
        raise RasterStackUnavailable(str(exc)) from exc

    env = dict(gdal_env or {})
    for cred_key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        cred_val = env.pop(cred_key, None)
        if cred_val is not None:
            os.environ[cred_key] = cred_val

    try:
        with rasterio.Env(**env), Reader(source) as cog:
            image = cog.preview(max_size=max_size)
    except RasterioIOError as exc:
        raise TileUnavailable(f"no readable {index} COG at the source") from exc

    if index in _COMPOSITE_RANGES:
        image.rescale(in_range=_COMPOSITE_RANGES[index])
        return image.render(img_format="JPEG", quality=85)

    from rio_tiler.colormap import cmap as default_cmaps

    params = render_params(index)
    vmin, vmax = cast("tuple[float, float]", params["rescale"])
    colormap = default_cmaps.get(str(params["colormap_name"]).lower())
    image.rescale(in_range=((vmin, vmax),))
    return image.render(img_format="JPEG", quality=85, colormap=colormap)


def render_tile(
    source: str,
    *,
    index: str,
    z: int,
    x: int,
    y: int,
    gdal_env: dict[str, str] | None = None,
) -> bytes:
    """Render one XYZ tile of `index` from the COG at `source` (a local path or a `/vsis3/` URI):
    read the window, rescale to the locked display range, apply the index colormap, return PNG
    bytes. Raises RasterStackUnavailable without the `geo` extra, TileUnavailable for a missing COG
    or an out-of-coverage tile."""
    try:
        import rasterio
        from rasterio.errors import RasterioIOError
        from rio_tiler.colormap import cmap as default_cmaps
        from rio_tiler.errors import TileOutsideBounds
        from rio_tiler.io import Reader
    except ImportError as exc:
        raise RasterStackUnavailable(str(exc)) from exc

    # rasterio 1.4 refuses AWS credentials as rasterio.Env options; GDAL still reads them from the
    # process environment for /vsis3/. Move any credential keys into os.environ and pass only the
    # remaining endpoint/addressing options to the Env.
    env = dict(gdal_env or {})
    for cred_key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        cred_val = env.pop(cred_key, None)
        if cred_val is not None:
            os.environ[cred_key] = cred_val

    try:
        with rasterio.Env(**env), Reader(source) as cog:
            image = cog.tile(x, y, z)
    except TileOutsideBounds as exc:
        raise TileUnavailable(f"tile {z}/{x}/{y} is outside {index} coverage") from exc
    except RasterioIOError as exc:
        raise TileUnavailable(f"no readable {index} COG at the source") from exc

    if index in _COMPOSITE_RANGES:
        image.rescale(in_range=_COMPOSITE_RANGES[index])
        return image.render(img_format="PNG")

    params = render_params(index)
    vmin, vmax = cast("tuple[float, float]", params["rescale"])
    colormap = default_cmaps.get(str(params["colormap_name"]).lower())

    image.rescale(in_range=((vmin, vmax),))
    return image.render(img_format="PNG", colormap=colormap)


def render_mask_tile(
    source: str,
    *,
    z: int,
    x: int,
    y: int,
    gdal_env: dict[str, str] | None = None,
) -> bytes:
    """Render one XYZ tile of the cloud-honesty overlay (backlog 0046) from the boolean mask COG at
    `source` (a local path or a `/vsis3/` URI): read the window and paint a semi-transparent hatch
    over the pixels the mask marks as cloud/SCL-masked, transparent everywhere else.

    The mask COG's footprint *is* the masked region (finite where masked, NoData elsewhere - see
    `rs_analysis.cog.cloud_mask_raster`), so there is no colormap or rescale: the masked pixels come
    straight from rio-tiler's NoData mask and only the alpha channel carries the pattern. Because
    the key already pins field/scene/geometry version, the overlay can never show a mask from a
    different pass. Raises RasterStackUnavailable without the `geo` extra, TileUnavailable for a
    missing COG or an out-of-coverage tile."""
    try:
        import rasterio
        from rasterio.errors import RasterioIOError
        from rio_tiler.errors import TileOutsideBounds
        from rio_tiler.io import Reader
        from rio_tiler.utils import render as render_rgba
    except ImportError as exc:
        raise RasterStackUnavailable(str(exc)) from exc

    env = dict(gdal_env or {})
    for cred_key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        cred_val = env.pop(cred_key, None)
        if cred_val is not None:
            os.environ[cred_key] = cred_val

    try:
        with rasterio.Env(**env), Reader(source) as cog:
            image = cog.tile(x, y, z)
    except TileOutsideBounds as exc:
        raise TileUnavailable(f"tile {z}/{x}/{y} is outside cloud-mask coverage") from exc
    except RasterioIOError as exc:
        raise TileUnavailable("no readable cloud-mask COG at the source") from exc

    # rio-tiler carries NoData as the masked-array mask; a valid (non-masked) pixel is one the mask
    # COG marked cloud/SCL-masked. Read the band's mask directly so the degenerate no-NoData case
    # stays explicit rather than hatching a whole tile.
    masked = ~np.ma.getmaskarray(image.array)[0]
    rgb, alpha = _hatch_overlay(masked)
    return cast("bytes", render_rgba(rgb, mask=alpha, img_format="PNG"))
