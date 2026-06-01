"""Slippy-map (XYZ) tile geometry, pure stdlib - no raster deps. Turns a tile request into the
WGS84 window the tiler reads from an index COG."""

from __future__ import annotations

import math


def tile_to_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """The WGS84 lon/lat bbox of an XYZ tile: (min_lon, min_lat, max_lon, max_lat). Standard Web
    Mercator slippy-map convention (y increases southward)."""
    n = 2**z

    def _lon(xt: float) -> float:
        return xt / n * 360.0 - 180.0

    def _lat(yt: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yt / n))))

    return _lon(x), _lat(y + 1), _lon(x + 1), _lat(y)
