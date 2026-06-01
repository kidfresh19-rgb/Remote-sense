"""Tile rendering for the tiler (L5). `render_params` is pure (the rescale + colormap an index is
drawn with, from the locked display range); `render_tile` reads an index COG window and colorizes
it with rio-tiler, which needs the raster stack (the `geo` extra, in-container only)."""

from __future__ import annotations

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

    params = render_params(index)
    vmin, vmax = params["rescale"]  # type: ignore[misc]
    # rio-tiler's registry keys are lowercase matplotlib names (the locked names are mixed-case).
    colormap = default_cmaps.get(str(params["colormap_name"]).lower())

    try:
        with rasterio.Env(**(gdal_env or {})), Reader(source) as cog:
            image = cog.tile(x, y, z)
    except TileOutsideBounds as exc:
        raise TileUnavailable(f"tile {z}/{x}/{y} is outside {index} coverage") from exc
    except RasterioIOError as exc:
        raise TileUnavailable(f"no readable {index} COG at the source") from exc

    image.rescale(in_range=((vmin, vmax),))
    return image.render(img_format="PNG", colormap=colormap)
