"""Tile rendering for the tiler (L5). `render_params` is pure (the rescale + colormap an index is
drawn with, from the locked display range); `render_tile` reads an index COG window and colorizes
it with rio-tiler, which needs the raster stack (the `geo` extra, in-container only)."""

from __future__ import annotations

import os
from typing import cast

from rs_analysis import get_colormap


class RasterStackUnavailable(RuntimeError):
    """The raster stack (`rasterio` / `rio-tiler`, the `geo` extra) is not installed - the host
    case. The route surfaces this as 503."""


class TileUnavailable(RuntimeError):
    """No readable COG at the source, or the requested tile lies outside its coverage. The route
    surfaces this as 404."""


def render_params(index: str) -> dict[str, object]:
    """The rescale range + colormap name a tile of `index` is rendered with (matplotlib / rio-tiler
    convention), read from the locked display range. Raises KeyError for an unknown index."""
    if index == "rgb":
        return {"colormap_name": None, "rescale": (0.0, 0.3)}
    colormap = get_colormap(index)
    return {"colormap_name": colormap.colormap, "rescale": (colormap.vmin, colormap.vmax)}


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

    if index == "rgb":
        image.rescale(in_range=((0.0, 0.3), (0.0, 0.3), (0.0, 0.3)))
        return image.render(img_format="PNG")

    params = render_params(index)
    vmin, vmax = cast("tuple[float, float]", params["rescale"])
    colormap = default_cmaps.get(str(params["colormap_name"]).lower())

    image.rescale(in_range=((vmin, vmax),))
    return image.render(img_format="PNG", colormap=colormap)
