"""Pure geospatial helpers for ingestion: CRS normalisation, UTM zone selection, geometry
validation and the field-within-farm nesting check. No DB, no network, no rasterio - this
is unit-testable with synthetic geometries.

CRS rule (CLAUDE.md §2): store the source CRS; reproject to UTM before any area/distance
math. Zimbabwe straddles two UTM zones, split at 30°E: EPSG:32735 (35S) west, 32736 (36S)
east. We normalise stored geometry to WGS84 (EPSG:4326) and reproject on demand for area."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import pyproj
import shapely
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.validation import explain_validity

WGS84_EPSG = 4326
UTM_35S_EPSG = 32735  # west of 30°E
UTM_36S_EPSG = 32736  # east of 30°E
ZIMBABWE_ZONE_SPLIT_LON = 30.0

# Survey imprecision tolerance: a field may overhang its farm boundary by up to this
# fraction of the field's own area before we call it a nesting violation (DI-3).
DEFAULT_NESTING_TOLERANCE = 0.02
# Two boundaries are treated as the same geometry version when their symmetric difference
# is below this fraction of the field area - guards against float noise re-versioning (DI-5).
DEFAULT_GEOMETRY_EQUALITY_TOLERANCE = 0.001


def parse_epsg(crs: str | int) -> int:
    """Accept 'EPSG:4326', 'epsg:4326', '4326' or 4326 and return the integer code."""
    if isinstance(crs, int):
        return crs
    text = crs.strip().upper()
    if text.startswith("EPSG:"):
        text = text[len("EPSG:") :]
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Unrecognised CRS {crs!r}; expected an EPSG code") from exc


def utm_epsg_for(lon: float, lat: float) -> int:
    """Working UTM CRS for a centroid. Zimbabwe is southern hemisphere; we split on 30°E.
    Longitudes east of the split use zone 36S, otherwise 35S (PLAN §6)."""
    return UTM_36S_EPSG if lon >= ZIMBABWE_ZONE_SPLIT_LON else UTM_35S_EPSG


def to_shape(geometry: dict[str, Any]) -> BaseGeometry:
    """GeoJSON geometry mapping -> shapely geometry."""
    return shape(geometry)


def to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    """shapely geometry -> GeoJSON geometry mapping."""
    return mapping(geom)


# AOI Studio result/search caches key on geometry (ADR 0011). 6 dp is sub-pixel at 10 m
# (~0.11 m at the equator), so coordinate serialisation noise and sub-tolerance jitter collapse
# to one cache key while a genuinely redrawn polygon misses.
_CANONICAL_HASH_DP = 6


def canonical_geometry_hash(geometry: dict[str, Any]) -> str:
    """A stable content hash for an AOI polygon, invariant to coordinate serialisation and
    sub-tolerance jitter (ADR 0011 cache keys). Rounds coordinates to `_CANONICAL_HASH_DP`
    decimal places, canonicalises ring winding and the start vertex with shapely's `normalize`,
    then hashes the WKB. Two encodings of the same polygon hash equal; a different polygon does
    not. Geometry only - never mixed with mutable inputs such as dates or scene ids, so every
    key built from it is immutable."""
    geom = to_shape(geometry)
    rounded = shapely.transform(geom, lambda coords: np.round(coords, _CANONICAL_HASH_DP))
    return hashlib.sha256(rounded.normalize().wkb).hexdigest()


@lru_cache(maxsize=64)
def _transformer(src_epsg: int, dst_epsg: int) -> pyproj.Transformer:
    """Cache transformers by EPSG pair. Building one is non-trivial and ingestion reprojects
    repeatedly (area, nesting, equivalence) across the same handful of Zimbabwean zones."""
    return pyproj.Transformer.from_crs(src_epsg, dst_epsg, always_xy=True)


def reproject(geom: BaseGeometry, src_epsg: int, dst_epsg: int) -> BaseGeometry:
    """Reproject a shapely geometry between EPSG codes. always_xy keeps lon/lat order."""
    if src_epsg == dst_epsg:
        return geom
    return shapely_transform(_transformer(src_epsg, dst_epsg).transform, geom)


def to_wgs84(geom: BaseGeometry, src_epsg: int) -> BaseGeometry:
    """Normalise an incoming geometry to WGS84 for canonical storage (PostGIS SRID 4326)."""
    return reproject(geom, src_epsg, WGS84_EPSG)


def area_m2(geom_wgs84: BaseGeometry) -> float:
    """Area in square metres, computed in the correct UTM zone for the geometry's centroid.
    Never trust degrees-squared (CLAUDE.md §2 - reproject before any area math)."""
    centroid = geom_wgs84.centroid
    working = utm_epsg_for(centroid.x, centroid.y)
    return reproject(geom_wgs84, WGS84_EPSG, working).area


@dataclass
class GeometryValidation:
    """Outcome of validating one incoming geometry. `repaired` is set when a self-touching
    or otherwise invalid ring was healed with a zero-width buffer; we keep the healed shape
    but record that it happened so ingestion can flag it."""

    ok: bool
    reason: str | None = None
    geometry: BaseGeometry | None = None
    repaired: bool = False
    warnings: list[str] = field(default_factory=list)


def validate_geometry(
    geometry: dict[str, Any],
    *,
    src_epsg: int,
    min_area_m2: float = 1.0,
    attempt_repair: bool = True,
) -> GeometryValidation:
    """Validate and normalise one incoming polygonal geometry to WGS84.

    Rejects non-polygonal, empty, or degenerate (near-zero area) geometry. Self-intersecting
    rings are repaired with buffer(0) when `attempt_repair` is set, and the repair is
    surfaced as a warning rather than silently swallowed."""
    gtype = geometry.get("type")
    if gtype not in {"Polygon", "MultiPolygon"}:
        return GeometryValidation(ok=False, reason=f"geometry must be polygonal, got {gtype!r}")

    try:
        geom = to_shape(geometry)
    except Exception as exc:  # malformed coordinates / structure
        return GeometryValidation(ok=False, reason=f"unparseable geometry: {exc}")

    if geom.is_empty:
        return GeometryValidation(ok=False, reason="geometry is empty")

    warnings: list[str] = []
    repaired = False
    if not geom.is_valid:
        if not attempt_repair:
            return GeometryValidation(ok=False, reason=explain_validity(geom))
        healed = geom.buffer(0)
        if healed.is_empty or not healed.is_valid:
            return GeometryValidation(
                ok=False, reason=f"unrepairable geometry: {explain_validity(geom)}"
            )
        warnings.append(f"geometry repaired (was invalid: {explain_validity(geom)})")
        geom = healed
        repaired = True

    geom_wgs84 = to_wgs84(geom, src_epsg)
    if area_m2(geom_wgs84) < min_area_m2:
        return GeometryValidation(
            ok=False, reason=f"geometry area below {min_area_m2} m² (degenerate)"
        )

    return GeometryValidation(ok=True, geometry=geom_wgs84, repaired=repaired, warnings=warnings)


def nesting_fraction_outside(field_wgs84: BaseGeometry, farm_wgs84: BaseGeometry) -> float:
    """Fraction of the field's area that falls outside the farm boundary. 0.0 means fully
    nested; small positive values are survey overhang (DI-3)."""
    field_area = area_m2(field_wgs84)
    if field_area <= 0:
        return 1.0
    outside = field_wgs84.difference(farm_wgs84)
    if outside.is_empty:
        return 0.0
    return area_m2(outside) / field_area


def is_nested(
    field_wgs84: BaseGeometry,
    farm_wgs84: BaseGeometry,
    *,
    tolerance: float = DEFAULT_NESTING_TOLERANCE,
) -> bool:
    """True when a field sits within its farm boundary to within the overhang tolerance."""
    return nesting_fraction_outside(field_wgs84, farm_wgs84) <= tolerance


def geometries_equivalent(
    a_wgs84: BaseGeometry,
    b_wgs84: BaseGeometry,
    *,
    tolerance: float = DEFAULT_GEOMETRY_EQUALITY_TOLERANCE,
) -> bool:
    """True when two boundaries are the same to within float noise. Used to decide whether
    an incoming boundary is a genuine change that should bump geometry_version (DI-5)."""
    if a_wgs84.is_empty or b_wgs84.is_empty:
        return a_wgs84.is_empty and b_wgs84.is_empty
    ref_area = max(area_m2(a_wgs84), area_m2(b_wgs84))
    if ref_area <= 0:
        return True
    sym_diff = a_wgs84.symmetric_difference(b_wgs84)
    if sym_diff.is_empty:
        return True
    return area_m2(sym_diff) / ref_area <= tolerance
