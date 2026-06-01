"""Render parameters for an index tile, derived from the locked per-index colormap (rs_analysis).
Pure - the rio-tiler rendering itself lives in main.py and needs the raster stack."""

from __future__ import annotations

from rs_analysis import get_colormap


def render_params(index: str) -> dict[str, object]:
    """The rescale range + colormap name a tile of `index` is rendered with (matplotlib / rio-tiler
    convention), read from the locked display range. Raises KeyError for an unknown index."""
    colormap = get_colormap(index)
    return {"colormap_name": colormap.colormap, "rescale": (colormap.vmin, colormap.vmax)}
