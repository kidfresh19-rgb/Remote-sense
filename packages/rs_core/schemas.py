"""Pydantic v2 boundary schemas for ingestion (L1).

DI-1 (confirmed 2026-06-04, ADR 0006): FarmIn is our vendor-neutral ingestion schema. The AgriTrack
onboarding contract (POST /api/v1/mobile/sync) maps onto it in the inbound adapter
(services/api/integrations.py), so the validation + storage logic depends only on (a) a stable
canonical farm id and (b) polygonal geometry with a declared CRS, never on the vendor's
field names."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_POLYGONAL = {"Polygon", "MultiPolygon"}


def _check_polygonal(v: dict[str, Any]) -> dict[str, Any]:
    gtype = v.get("type")
    if gtype not in _POLYGONAL:
        raise ValueError(f"geometry must be Polygon or MultiPolygon, got {gtype!r}")
    return v


class FieldIn(BaseModel):
    """One inner field within a farm. Geometry is GeoJSON in the declared CRS."""

    model_config = ConfigDict(extra="ignore")

    canonical_field_id: str | None = None
    name: str | None = None
    crop: str | None = None
    geometry: dict[str, Any]
    crs: str = "EPSG:4326"

    _v_geom = field_validator("geometry")(staticmethod(_check_polygonal))


class FarmIn(BaseModel):
    """A farm onboarding payload. `boundary` may be omitted when the gateway sends only inner
    fields; in that case the farm boundary is derived from the union of its fields. A farm
    with neither a boundary nor fields is rejected (nothing to analyse, DI-4)."""

    model_config = ConfigDict(extra="ignore")

    canonical_farm_id: str = Field(min_length=1)
    agritrack_farmer_id: str | None = None
    name: str | None = None
    region: str | None = None
    boundary: dict[str, Any] | None = None
    crs: str = "EPSG:4326"
    fields: list[FieldIn] = Field(default_factory=list)

    @field_validator("boundary")
    @classmethod
    def _boundary_polygonal(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if v is None else _check_polygonal(v)


class FieldIngestReport(BaseModel):
    """Per-field outcome, surfaced so an operator can see exactly what ingestion did."""

    canonical_field_id: str | None
    field_id: str
    action: str  # created | updated_geometry | unchanged | derived_from_farm
    geometry_version: int
    repaired: bool = False
    warnings: list[str] = Field(default_factory=list)


class FarmIngestReport(BaseModel):
    """Full ingestion outcome for one farm."""

    canonical_farm_id: str
    farm_id: str
    action: str  # created | updated | unchanged
    working_crs: str
    fields: list[FieldIngestReport] = Field(default_factory=list)
