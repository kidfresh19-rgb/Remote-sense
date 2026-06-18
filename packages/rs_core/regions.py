"""Pure region-cluster geometry: layer loading, centroid assignment, and the Natural Region
composition (comparison groups, PRD 0002 / ADR 0010). No DB, no network - unit-testable
with synthetic geometries, exactly like `rs_core.geo`, which it reuses for validation, reprojection,
and UTM zone selection.

Two edges are served here:
- the seed and upload loader (`read_region_layer`): every feature of a `.zip` shapefile / GeoJSON /
  GeoPackage, validated and normalised to WGS84, with broken features skipped and reported rather
  than fatal (backlog 0005), and a missing CRS raised rather than guessed.
- assignment (`assign_centroid`): the one membership rule for every boundary `source` - the region
  whose polygon contains a farm's centroid, with a boundary-adjacent flag for edge cases.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from shapely.geometry import MultiPolygon, Point
from shapely.geometry.base import BaseGeometry

from rs_core.geo import WGS84_EPSG, reproject, utm_epsg_for, validate_geometry


class RegionSource(StrEnum):
    """How a region boundary came to exist (ADR 0010 amendment). All three are the same analytical
    reference-geometry category; the tag only drives provenance and the map `source` filter."""

    SEEDED = "seeded"
    UPLOADED = "uploaded"
    DRAWN = "drawn"


class RegionLayerError(ValueError):
    """A region layer could not be read: missing/unknown CRS, an unreadable or empty layer, or a
    name column that is not present. Raised rather than guessing (a wrong CRS silently mis-locates
    every region)."""


@dataclass(frozen=True)
class ParsedRegionFeature:
    """One validated feature ready to persist: its name, a WGS84 MultiPolygon, and the CRS it was
    read from (recorded for provenance, never inferred)."""

    name: str
    geometry: MultiPolygon
    source_crs: str


@dataclass(frozen=True)
class SkippedFeature:
    """A feature that could not be used, kept for the skip-and-report contract (backlog 0005) so one
    bad polygon never loses the whole layer."""

    index: int
    name: str | None
    reason: str


@dataclass(frozen=True)
class LoadedRegionLayer:
    """The outcome of reading a layer: the usable features and the per-feature skip report."""

    features: list[ParsedRegionFeature]
    skipped: list[SkippedFeature]
    source_crs: str


def as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    """Coerce a Polygon to a single-part MultiPolygon; pass a MultiPolygon through. The store column
    is `geometry(MultiPolygon, 4326)`, which rejects a bare Polygon, so every boundary is normalised
    to MultiPolygon before persistence."""
    if isinstance(geom, MultiPolygon):
        return geom
    return MultiPolygon([geom])


def read_region_layer(path: Path | str, *, name_column: str) -> LoadedRegionLayer:
    """Read every feature of a `.zip` shapefile / GeoJSON / GeoPackage via geopandas, validate and
    normalise each to a WGS84 MultiPolygon reusing `validate_geometry`, and map names from
    `name_column`. Broken or unnamed features are skipped and reported, never fatal. Raises
    `RegionLayerError` on a missing/unknown CRS, an unreadable or empty layer, or an absent name
    column - the layer-level failures the analyst must fix before any feature can be trusted.

    geopandas is the `geo` extra; imported lazily so importing this module never requires it."""
    import geopandas as gpd  # lazy: the heavy geo stack is only needed to read a file
    import pandas as pd
    from shapely.geometry import mapping

    try:
        gdf = gpd.read_file(str(path))
    except Exception as exc:  # unreadable / unsupported file
        raise RegionLayerError(f"could not read region layer {path!r}: {exc}") from exc

    if gdf.crs is None:
        raise RegionLayerError(
            f"region layer {path!r} has no CRS; provide a .prj or a CRS rather than guessing"
        )
    epsg = gdf.crs.to_epsg()
    if epsg is None:
        raise RegionLayerError(
            f"region layer {path!r} CRS {gdf.crs!r} has no EPSG code; reproject it to a known CRS"
        )
    if name_column not in gdf.columns:
        raise RegionLayerError(
            f"name column {name_column!r} not in layer columns {list(gdf.columns)}"
        )

    source_crs = f"EPSG:{epsg}"
    geom_col = gdf.geometry.name
    features: list[ParsedRegionFeature] = []
    skipped: list[SkippedFeature] = []
    for position, (_, row) in enumerate(gdf.iterrows()):
        raw_name = row[name_column]
        name = None if pd.isna(raw_name) else str(raw_name).strip()
        geom = row[geom_col]
        if geom is None or geom.is_empty:
            skipped.append(SkippedFeature(position, name, "empty or missing geometry"))
            continue
        if not name:
            skipped.append(
                SkippedFeature(position, None, f"missing name in column {name_column!r}")
            )
            continue
        validation = validate_geometry(mapping(geom), src_epsg=epsg)
        if not validation.ok or validation.geometry is None:
            skipped.append(SkippedFeature(position, name, validation.reason or "invalid geometry"))
            continue
        features.append(
            ParsedRegionFeature(
                name=name,
                geometry=as_multipolygon(validation.geometry),
                source_crs=source_crs,
            )
        )

    if not features:
        raise RegionLayerError(f"region layer {path!r} has no usable features (all skipped)")
    return LoadedRegionLayer(features=features, skipped=skipped, source_crs=source_crs)


@dataclass(frozen=True)
class RegionAssignment:
    """The region a farm's centroid falls in, plus whether that centroid sits within the configured
    edge tolerance of the region's boundary (a flag for a human sanity check, never a rejection)."""

    region_boundary_id: uuid.UUID
    boundary_adjacent: bool


def assign_centroid(
    lon: float,
    lat: float,
    candidates: Sequence[tuple[uuid.UUID, BaseGeometry]],
    *,
    edge_tolerance_m: float,
) -> RegionAssignment | None:
    """The region whose polygon contains a farm centroid (lon, lat), or None when the centroid falls
    outside every candidate. `candidates` are `(region_boundary_id, WGS84 polygon)`. The
    one membership rule for every `source` (ADR 0010). Deterministic: candidates are considered in a
    stable id order, so a centroid that lands exactly on a shared edge always resolves to the same
    region. `boundary_adjacent` is true when the centroid is within `edge_tolerance_m` of the chosen
    region's boundary, measured in the working UTM zone (never in degrees, §2)."""
    point = Point(lon, lat)
    for region_id, polygon in sorted(candidates, key=lambda c: str(c[0])):
        if not polygon.covers(point):
            continue
        utm = utm_epsg_for(lon, lat)
        point_utm = reproject(point, WGS84_EPSG, utm)
        boundary_utm = reproject(polygon.boundary, WGS84_EPSG, utm)
        adjacent = bool(point_utm.distance(boundary_utm) <= edge_tolerance_m)
        return RegionAssignment(region_boundary_id=region_id, boundary_adjacent=adjacent)
    return None


# Sub-1% slivers are float noise where a created region grazes a neighbouring NR; drop them
# so a composition is not cluttered with spurious zones.
_MIN_NR_SHARE = 0.01


def natural_region_composition(
    boundary_wgs84: BaseGeometry,
    nr_polygons: Sequence[tuple[str, BaseGeometry]],
) -> tuple[dict[str, float], str | None]:
    """Area-weighted Natural Region composition: the fraction of the boundary's
    area falling in each overlapping NR, plus the dominant (largest-share) NR. A boundary may span
    several NRs; it is never clipped or rejected (ADR 0010 amendment) - the composition records the
    spread so the analysis layer can benchmark like-with-like. Areas are computed in the boundary's
    working UTM zone (§2, never degrees). A boundary overlapping no NR - or with no NR layer seeded
    yet - yields ({}, None), so composition degrades gracefully."""
    if not nr_polygons:
        return {}, None
    centroid = boundary_wgs84.centroid
    utm = utm_epsg_for(centroid.x, centroid.y)
    boundary_utm = reproject(boundary_wgs84, WGS84_EPSG, utm)
    total = boundary_utm.area
    if total <= 0:
        return {}, None

    shares: dict[str, float] = {}
    for name, polygon in nr_polygons:
        intersection = boundary_utm.intersection(reproject(polygon, WGS84_EPSG, utm))
        if intersection.is_empty:
            continue
        fraction = intersection.area / total
        if fraction > 0:
            shares[name] = shares.get(name, 0.0) + fraction
    if not shares:
        return {}, None

    kept = {name: round(share, 4) for name, share in shares.items() if share >= _MIN_NR_SHARE}
    if (
        not kept
    ):  # everything is sub-threshold sliver; keep the single largest so dominant is defined
        top = max(shares, key=lambda name: shares[name])
        kept = {top: round(shares[top], 4)}
    dominant = max(kept, key=lambda name: kept[name])
    return kept, dominant
