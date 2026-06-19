"""Region-cluster creation endpoints (comparison groups, PRD 0002 slice 5): analyst draw-to-create
and admin multi/single-feature upload. Region boundaries are remote-sense-owned analytical reference
geometry, served by the internal BFF and never pushed to the gateway (invariant 6, ADR 0010). The
draw endpoint accepts a GeoJSON polygon (the frontend compiles a radius circle to a polygon first);
the upload endpoint reads the raw file body via geopandas (no multipart dependency added). RBAC:
drawing / single-feature is analyst-level (create_region_cluster), bulk upload is admin-level
(upload_region_boundary) - PRD 0002 Open Item 3, RBAC mappings confirmed 2026-06-19."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, StringConstraints
from rs_core.config import get_settings
from rs_core.geo import validate_geometry
from rs_core.models import RegionBoundary, RegionBoundaryLayer
from rs_core.regions import RegionLayerError, SkippedFeature, as_multipolygon, read_region_layer
from rs_core.repositories import (
    create_drawn_region,
    create_uploaded_layer,
    natural_region_polygons,
    recompute_farm_region_assignments,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.workspace.deps import (
    CreateRegionClusterPrincipal,
    SessionDep,
    UploadRegionBoundaryPrincipal,
)

router = APIRouter(tags=["workspace"])

_WGS84_EPSG = 4326


class RegionDrawRequest(BaseModel):
    """An analyst-drawn region. `geometry` is a GeoJSON Polygon or MultiPolygon (the frontend
    compiles a radius circle to a polygon before sending, so the server sees one rule)."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    geometry: dict[str, Any]


class RegionOut(BaseModel):
    region_boundary_id: str
    layer_id: str
    name: str
    source: str
    nr_composition: dict[str, float]
    dominant_nr: str | None
    farms_captured: int


class SkippedFeatureOut(BaseModel):
    index: int
    name: str | None
    reason: str


class RegionUploadOut(BaseModel):
    layer_id: str
    created: int
    skipped: list[SkippedFeatureOut]
    farms_assigned: int


def _skipped_out(skipped: SkippedFeature) -> SkippedFeatureOut:
    return SkippedFeatureOut(index=skipped.index, name=skipped.name, reason=skipped.reason)


async def create_drawn_region_from_geojson(
    session: AsyncSession,
    *,
    name: str,
    geometry: dict[str, Any],
    creator: str | None,
    edge_tolerance_m: float,
) -> tuple[RegionBoundary, int]:
    """Validate a GeoJSON polygon, persist it as a `source=drawn` region, and assign the farms it
    captures. Returns (boundary, farms_captured). Raises RegionLayerError on invalid geometry. The
    boundary is never clipped for spanning Natural Regions - the composition records the spread."""
    validation = validate_geometry(geometry, src_epsg=_WGS84_EPSG)
    if not validation.ok or validation.geometry is None:
        raise RegionLayerError(validation.reason or "invalid geometry")
    nr_polygons = await natural_region_polygons(session)
    boundary = await create_drawn_region(
        session,
        name=name,
        geometry=as_multipolygon(validation.geometry),
        creator=creator,
        nr_polygons=nr_polygons,
    )
    captured = await recompute_farm_region_assignments(
        session, layer_id=boundary.layer_id, edge_tolerance_m=edge_tolerance_m
    )
    return boundary, captured


async def ingest_region_upload(
    session: AsyncSession,
    *,
    raw: bytes,
    filename: str,
    name_column: str,
    creator: str | None,
    edge_tolerance_m: float,
) -> tuple[RegionBoundaryLayer, int, list[SkippedFeature], int]:
    """Read an uploaded boundary file (raw bytes) with geopandas, persist its valid features as a
    `source=uploaded` layer, and assign captured farms. Returns (layer, created, skipped,
    farms_assigned). Raises RegionLayerError on an unreadable layer, missing CRS, or absent name
    column. The file goes to a temp path (its suffix selects the driver) and is removed."""
    suffix = Path(filename).suffix or ".geojson"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(raw)
        loaded = read_region_layer(path, name_column=name_column)
    nr_polygons = await natural_region_polygons(session)
    layer = await create_uploaded_layer(
        session, name=filename, features=loaded.features, creator=creator, nr_polygons=nr_polygons
    )
    assigned = await recompute_farm_region_assignments(
        session, layer_id=layer.id, edge_tolerance_m=edge_tolerance_m
    )
    return layer, len(loaded.features), loaded.skipped, assigned


@router.post("/regions/draw", response_model=RegionOut, status_code=status.HTTP_201_CREATED)
async def draw_region_endpoint(
    payload: RegionDrawRequest,
    principal: CreateRegionClusterPrincipal,
    session: SessionDep,
) -> RegionOut:
    """Persist an analyst-drawn region (create_region_cluster). The viewed farm is only the entry
    point; the region is free-standing and reusable, owned by no farm (ADR 0010 amendment)."""
    try:
        boundary, captured = await create_drawn_region_from_geojson(
            session,
            name=payload.name,
            geometry=payload.geometry,
            creator=principal.subject,
            edge_tolerance_m=get_settings().region_boundary_adjacent_tolerance_m,
        )
    except RegionLayerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return RegionOut(
        region_boundary_id=str(boundary.id),
        layer_id=str(boundary.layer_id),
        name=boundary.name,
        source=boundary.source,
        nr_composition=boundary.nr_composition,
        dominant_nr=boundary.dominant_nr,
        farms_captured=captured,
    )


@router.post("/regions/upload", response_model=RegionUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_region_layer_endpoint(
    request: Request,
    principal: UploadRegionBoundaryPrincipal,
    session: SessionDep,
    name_column: str = Query(..., description="layer attribute that names each region"),
    filename: str = Query(
        "upload.geojson", description="original filename; its suffix picks driver"
    ),
) -> RegionUploadOut:
    """Persist a multi/single-feature boundary upload (upload_region_boundary). The request body is
    the file bytes (.zip shapefile / GeoJSON / GeoPackage); geopandas reads every feature; broken
    features are skipped and reported, never fatal to the layer."""
    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty upload body")
    try:
        layer, created, skipped, assigned = await ingest_region_upload(
            session,
            raw=raw,
            filename=filename,
            name_column=name_column,
            creator=principal.subject,
            edge_tolerance_m=get_settings().region_boundary_adjacent_tolerance_m,
        )
    except RegionLayerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return RegionUploadOut(
        layer_id=str(layer.id),
        created=created,
        skipped=[_skipped_out(item) for item in skipped],
        farms_assigned=assigned,
    )
