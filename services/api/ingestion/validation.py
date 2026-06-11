"""Pure payload + geometry validation for ingestion: WGS84 normalisation, farm-boundary
resolution (declared or derived, DI-4), and the field-within-farm nesting guard (DI-3). No
database access; everything here is testable with shapes alone."""

from __future__ import annotations

from rs_core.geo import (
    GeometryValidation,
    is_nested,
    nesting_fraction_outside,
    parse_epsg,
    validate_geometry,
)
from rs_core.schemas import FarmIn, FieldIn
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


class IngestionError(ValueError):
    """A payload that fails validation. Surfaced to the caller as HTTP 422 - ingestion
    rejects bad geometry rather than storing it (Phase 1 definition of done)."""


def _as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    """Storage column is MULTIPOLYGON; promote a single Polygon so every row is uniform."""
    if geom.geom_type == "MultiPolygon":
        return geom
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    raise IngestionError(f"expected polygonal geometry for storage, got {geom.geom_type}")


def _crs_str(epsg: int) -> str:
    return f"EPSG:{epsg}"


def _validate_or_raise(geometry: dict, *, src_epsg: int, label: str) -> GeometryValidation:
    result = validate_geometry(geometry, src_epsg=src_epsg)
    if not result.ok or result.geometry is None:
        raise IngestionError(f"{label}: {result.reason}")
    return result


def _validate_fields(payload: FarmIn) -> list[tuple[FieldIn, GeometryValidation]]:
    out: list[tuple[FieldIn, GeometryValidation]] = []
    for idx, f in enumerate(payload.fields):
        label = f"field[{idx}]" + (f" ({f.canonical_field_id})" if f.canonical_field_id else "")
        result = _validate_or_raise(f.geometry, src_epsg=parse_epsg(f.crs), label=label)
        out.append((f, result))
    return out


def _resolve_farm_boundary(
    payload: FarmIn, validated_fields: list[tuple[FieldIn, GeometryValidation]]
) -> BaseGeometry:
    """The farm boundary, in WGS84. Use the declared boundary when present, otherwise derive
    it from the union of the inner fields. Reject a farm with neither (DI-4)."""
    if payload.boundary is not None:
        return _validate_or_raise(
            payload.boundary, src_epsg=parse_epsg(payload.crs), label="farm boundary"
        ).geometry  # type: ignore[return-value]
    if validated_fields:
        return unary_union([v.geometry for _, v in validated_fields])
    raise IngestionError("farm has neither a boundary nor any fields - nothing to analyse")


def _check_nesting(
    validated_fields: list[tuple[FieldIn, GeometryValidation]],
    farm_geom: BaseGeometry,
    *,
    derived_from_farm: bool,
) -> None:
    """DI-3: every inner field must sit within the farm boundary to the overhang tolerance.
    Skipped when the farm boundary was itself derived from the fields (trivially nested)."""
    if derived_from_farm:
        return
    for f, v in validated_fields:
        assert v.geometry is not None
        if not is_nested(v.geometry, farm_geom):
            frac = nesting_fraction_outside(v.geometry, farm_geom)
            label = f.canonical_field_id or f.name or "<unnamed>"
            raise IngestionError(
                f"field {label!r} is not nested within the farm boundary "
                f"({frac:.1%} of its area lies outside the tolerance)"
            )
