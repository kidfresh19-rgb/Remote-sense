"""The normalized shapes every adapter must return. The contract: reflectance-corrected
data, in a known CRS, with a clear-pixel fraction and a provenance tag. Downstream code is
identical regardless of which endpoint served the data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import numpy as np
from pydantic import BaseModel, field_validator


class ProcessingMode(StrEnum):
    """How a result was produced. Stored as provenance so the engine knows whether to
    recompute (windowed_cog) or store as-is (server_compute)."""

    MOCK = "mock"
    SERVER_COMPUTE = "server_compute"
    WINDOWED_COG = "windowed_cog"


class Provenance(BaseModel):
    """Travels with every result and every stored analysis. Reproducibility is
    non-negotiable (CLAUDE.md invariant 5)."""

    provider: str
    provider_scene_id: str
    processing_mode: ProcessingMode
    accessed_at: datetime
    formula_version: str | None = None


class BBox(BaseModel):
    """WGS84 (EPSG:4326) lon/lat bounding box."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


class AOI(BaseModel):
    """Area of interest: a GeoJSON Polygon/MultiPolygon geometry plus its CRS. The field
    polygon (or a no-field farm boundary) is always the analysis unit."""

    geometry: dict[str, Any]
    crs: str = "EPSG:4326"

    @field_validator("geometry")
    @classmethod
    def _must_be_polygonal(cls, v: dict[str, Any]) -> dict[str, Any]:
        gtype = v.get("type")
        if gtype not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"AOI geometry must be Polygon or MultiPolygon, got {gtype!r}")
        return v


class TimeRange(BaseModel):
    """Resolved in local time by the caller, carried here in UTC (CLAUDE.md time rule)."""

    start: datetime
    end: datetime

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: datetime, info: Any) -> datetime:
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError("TimeRange.end must be >= start")
        return v


class SceneRef(BaseModel):
    """A scene reference from search: metadata only, no pixels."""

    scene_id: str
    provider: str
    sensing_datetime: datetime  # UTC
    footprint: dict[str, Any]  # GeoJSON geometry
    crs: str = "EPSG:4326"
    # Scene-level cloud %. NOTE: never used for masking decisions; per-AOI SCL is
    # authoritative (CLAUDE.md invariant 3). Kept only as a coarse search filter.
    scene_cloud_pct: float | None = None


class SceneMetadata(BaseModel):
    """Per-scene radiometric metadata. The offset + quantification value are read here,
    never hard-coded (CLAUDE.md invariant 2)."""

    scene_id: str
    quantification_value: float
    boa_add_offset: dict[str, float]  # per-band additive offset (−1000 from Baseline 04.00)
    processing_baseline: str | None = None
    crs: str = "EPSG:4326"
    scene_cloud_pct: float | None = None


@dataclass
class BandStack:
    """Reflectance-corrected bands keyed by Sentinel-2 band id (e.g. "B04", "B08").
    NoData is represented as np.nan, never 0. Resolution is the native resolution of the
    coarsest band present (no silent upsampling)."""

    bands: dict[str, np.ndarray]
    crs: str
    transform: tuple[float, float, float, float, float, float]  # affine (a,b,c,d,e,f)
    resolution_m: float


@dataclass
class NormalizedResult:
    """The uniform fetch result. Reflectance-corrected, known CRS, clear-pixel fraction,
    provenance. Every adapter returns exactly this shape."""

    scene_id: str
    aoi: AOI
    data: BandStack
    clear_fraction: float  # fraction of AOI pixels usable after SCL masking
    provenance: Provenance


@dataclass
class PreviewTile:
    """A fast, coarse rendered index tile for the map (PNG bytes + legend range)."""

    png: bytes
    index: str
    vmin: float
    vmax: float
    colormap: str
    provenance: Provenance


# A minimal valid 1x1 transparent PNG. The mock adapter returns this as a stand-in tile;
# real adapters render actual colorized index tiles via rio-tiler.
_BLANK_PNG: bytes = bytes(
    [
        0x89,
        0x50,
        0x4E,
        0x47,
        0x0D,
        0x0A,
        0x1A,
        0x0A,
        0x00,
        0x00,
        0x00,
        0x0D,
        0x49,
        0x48,
        0x44,
        0x52,
        0x00,
        0x00,
        0x00,
        0x01,
        0x00,
        0x00,
        0x00,
        0x01,
        0x08,
        0x06,
        0x00,
        0x00,
        0x00,
        0x1F,
        0x15,
        0xC4,
        0x89,
        0x00,
        0x00,
        0x00,
        0x0A,
        0x49,
        0x44,
        0x41,
        0x54,
        0x78,
        0x9C,
        0x63,
        0x00,
        0x01,
        0x00,
        0x00,
        0x05,
        0x00,
        0x01,
        0x0D,
        0x0A,
        0x2D,
        0xB4,
        0x00,
        0x00,
        0x00,
        0x00,
        0x49,
        0x45,
        0x4E,
        0x44,
        0xAE,
        0x42,
        0x60,
        0x82,
    ]
)
