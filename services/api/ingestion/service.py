"""The ingestion orchestrator and its entrypoint: validation, working-CRS resolution (DI-2),
and the idempotent farm/field upsert, reported as one FarmIngestReport. POST /ingest/farm is
EXTERNAL-FROZEN (CONTRACT.md): its route, schemas, status codes, and operation id must not
change."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import from_shape
from rs_core.db import get_session
from rs_core.geo import parse_epsg, utm_epsg_for
from rs_core.logging import get_logger
from rs_core.models import Farm, Field
from rs_core.schemas import FarmIn, FarmIngestReport, FieldIngestReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.ingestion.persistence import _match_existing, _upsert_field, get_or_create_farm
from services.api.ingestion.validation import (
    IngestionError,
    _as_multipolygon,
    _check_nesting,
    _crs_str,
    _resolve_farm_boundary,
    _validate_fields,
)

log = get_logger("ingestion")

router = APIRouter(prefix="/ingest", tags=["ingestion"])


async def ingest_farm(session: AsyncSession, payload: FarmIn) -> FarmIngestReport:
    """Validate and persist one farm. Idempotent: an identical payload yields all-`unchanged`
    field reports and no writes beyond a no-op identity refresh."""
    src_epsg = parse_epsg(payload.crs)
    validated_fields = _validate_fields(payload)

    declared_boundary = payload.boundary is not None
    farm_geom = _resolve_farm_boundary(payload, validated_fields)
    derived_field = not validated_fields  # DI-4: no inner fields → boundary is the field

    _check_nesting(validated_fields, farm_geom, derived_from_farm=not declared_boundary)

    centroid = farm_geom.centroid
    working_epsg = utm_epsg_for(centroid.x, centroid.y)
    working_crs = _crs_str(working_epsg)

    farm_stored = from_shape(_as_multipolygon(farm_geom), srid=4326)

    def _build_farm() -> Farm:
        return Farm(
            canonical_farm_id=payload.canonical_farm_id,
            agritrack_farmer_id=payload.agritrack_farmer_id,
            name=payload.name,
            region=payload.region,
            boundary=farm_stored,
            centroid_lon=centroid.x,
            centroid_lat=centroid.y,
            source_crs=_crs_str(src_epsg),
            working_crs=working_crs,
        )

    farm, created = await get_or_create_farm(
        session, canonical_farm_id=payload.canonical_farm_id, build=_build_farm
    )
    if created:
        farm_action = "created"
        existing_fields: list[Field] = []
    else:
        # Existing farm, or a concurrent first-create we lost: refresh the mutable copy and
        # reconcile its fields below.
        farm.name = payload.name
        farm.region = payload.region
        farm.agritrack_farmer_id = payload.agritrack_farmer_id
        farm.boundary = farm_stored
        farm.centroid_lon = centroid.x
        farm.centroid_lat = centroid.y
        farm.working_crs = working_crs
        farm_action = "updated"
        existing_fields = list(
            (await session.execute(select(Field).where(Field.farm_id == farm.id))).scalars()
        )

    by_canonical = {
        f.canonical_field_id: f for f in existing_fields if f.canonical_field_id is not None
    }
    unkeyed = [f for f in existing_fields if f.canonical_field_id is None]

    field_reports: list[FieldIngestReport] = []
    if derived_field:
        existing = next((f for f in existing_fields if f.derived_from_farm), None)
        field_reports.append(
            await _upsert_field(
                session,
                farm=farm,
                existing=existing,
                geom_wgs84=farm_geom,
                canonical_field_id=None,
                name=payload.name,
                crop=None,
                src_epsg=src_epsg,
                working_crs=working_crs,
                derived_from_farm=True,
                repaired=False,
                warnings=[],
            )
        )
    else:
        for f, v in validated_fields:
            assert v.geometry is not None
            existing = _match_existing(f.canonical_field_id, v.geometry, by_canonical, unkeyed)
            # Consume the match so two incoming fields can't both bind to one stored row.
            if existing is not None:
                if existing.canonical_field_id is not None:
                    by_canonical.pop(existing.canonical_field_id, None)
                else:
                    unkeyed.remove(existing)
            field_reports.append(
                await _upsert_field(
                    session,
                    farm=farm,
                    existing=existing,
                    geom_wgs84=v.geometry,
                    canonical_field_id=f.canonical_field_id,
                    name=f.name,
                    crop=f.crop,
                    src_epsg=parse_epsg(f.crs),
                    working_crs=working_crs,
                    derived_from_farm=False,
                    repaired=v.repaired,
                    warnings=v.warnings,
                )
            )

    if not created and all(r.action == "unchanged" for r in field_reports):
        farm_action = "unchanged"

    log.info(
        "ingest.farm",
        canonical_farm_id=payload.canonical_farm_id,
        action=farm_action,
        fields=len(field_reports),
        working_crs=working_crs,
    )
    return FarmIngestReport(
        canonical_farm_id=payload.canonical_farm_id,
        farm_id=str(farm.id),
        action=farm_action,
        working_crs=working_crs,
        fields=field_reports,
    )


@router.post("/farm", response_model=FarmIngestReport, status_code=status.HTTP_200_OK)
async def ingest_farm_endpoint(
    payload: FarmIn, session: AsyncSession = Depends(get_session)
) -> FarmIngestReport:
    """Ingest one farm. Idempotent under retries; bad geometry is rejected with 422.

    ⚑ CONFIRM (DI-1): this HTTP entrypoint mirrors the eventual webhook arrival path. The
    default arrival mode is DB polling (config `RS_ARRIVAL_SOURCE`); both paths share this
    same service, so swapping the trigger never touches ingestion logic."""
    try:
        return await ingest_farm(session, payload)
    except IngestionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
