"""Pure proxy-AOI generation for Ward Watch officer enrollment (PRD 0003 §8.2, backlog 0028).
Officers drop a centre pin and pick a field-size class rather than drawing polygons. This module
turns (lat, lon, size_class) into an area-correct proxy AOI.

CRS discipline (CLAUDE.md §2): the square is built in the appropriate UTM zone (EPSG:32735 west
of 30°E, EPSG:32736 east) so area is correct, then reprojected to WGS84 (4326) for storage.
No DB, no network - unit-testable on synthetic inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pyproj
import shapely.ops
from shapely.geometry import Polygon, mapping

from rs_core.geo import utm_epsg_for

# Sentinel-2 native resolution for vegetation indices.
_S2_PIXEL_M = 10.0
# One-pixel erosion buffer on all sides removes edge-contaminated pixels.
_EROSION_BUFFER_M = _S2_PIXEL_M
# Minimum usable pixel count below which a per-plot stat is flagged low quality (PRD 0003 §4).
MIN_USABLE_PIXELS = 3


class SizeClass(StrEnum):
    """Field-size classes officers pick at enrollment (PRD 0003 §8.2)."""

    BACKYARD = "backyard"
    SMALL = "small_holding"
    MEDIUM = "medium"
    LARGE = "large"


SIZE_CLASS_AREA_HA: dict[SizeClass, float] = {
    SizeClass.BACKYARD: 0.10,
    SizeClass.SMALL: 0.50,
    SizeClass.MEDIUM: 2.00,
}


@dataclass(frozen=True)
class ProxyAOI:
    """The proxy AOI generated from a centre pin and size class.

    `geometry` is a GeoJSON Polygon in EPSG:4326.
    `area_m2` is the UTM square area (area-correct before reprojection).
    `geometry_source` is always `officer_proxy`.
    `usable_pixel_count` estimates clear 10 m pixels after the erosion buffer;
    it feeds the pixel-quality flag (PRD 0003 §4)."""

    geometry: dict[str, Any]
    area_m2: float
    geometry_source: str
    usable_pixel_count: int

    @property
    def low_pixel_quality(self) -> bool:
        """True when too few clear pixels remain to trust a per-plot statistic (PRD 0003 §4)."""
        return self.usable_pixel_count < MIN_USABLE_PIXELS


def _transformer(from_epsg: int, to_epsg: int) -> pyproj.Transformer:
    return pyproj.Transformer.from_crs(from_epsg, to_epsg, always_xy=True)


def _square_in_utm(east_m: float, north_m: float, area_m2: float) -> Polygon:
    """Axis-aligned square centred on the UTM point with the given area."""
    half = math.sqrt(area_m2) / 2
    return Polygon(
        [
            (east_m - half, north_m - half),
            (east_m + half, north_m - half),
            (east_m + half, north_m + half),
            (east_m - half, north_m + half),
            (east_m - half, north_m - half),
        ]
    )


def _estimate_usable_pixels(area_m2: float) -> int:
    """10 m pixel count after eroding one pixel inward on all sides.

    side_m - 2*buffer gives the eroded side; pixels = (eroded_side / pixel_size)^2."""
    side = math.sqrt(area_m2)
    eroded = side - 2 * _EROSION_BUFFER_M
    if eroded <= 0:
        return 0
    return int((eroded / _S2_PIXEL_M) ** 2)


def proxy_aoi(
    lat: float,
    lon: float,
    size_class: SizeClass | str,
    *,
    area_ha: float | None = None,
) -> ProxyAOI:
    """Generate an area-correct proxy AOI from a centre pin and size class.

    The square is built in UTM (EPSG:32735 west of 30°E, EPSG:32736 east) then reprojected
    to WGS84 (4326) for storage. `size_class=SizeClass.LARGE` requires an explicit `area_ha`;
    all other classes ignore it.

    Raises ValueError for an unknown size class, LARGE with no area, or area <= 0."""
    sc = SizeClass(size_class)
    if sc is SizeClass.LARGE:
        if area_ha is None or area_ha <= 0:
            raise ValueError("SizeClass.LARGE requires a positive area_ha")
        target_ha = area_ha
    else:
        target_ha = SIZE_CLASS_AREA_HA[sc]

    area_m2 = target_ha * 10_000.0
    utm_epsg = utm_epsg_for(lon, lat)

    wgs84_to_utm = _transformer(4326, utm_epsg)
    utm_to_wgs84 = _transformer(utm_epsg, 4326)

    east_m, north_m = wgs84_to_utm.transform(lon, lat)
    square_utm = _square_in_utm(east_m, north_m, area_m2)
    square_4326 = shapely.ops.transform(utm_to_wgs84.transform, square_utm)

    return ProxyAOI(
        geometry=mapping(square_4326),
        area_m2=area_m2,
        geometry_source="officer_proxy",
        usable_pixel_count=_estimate_usable_pixels(area_m2),
    )
