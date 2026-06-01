"""Ingestion service (L1): turn a gateway farm payload into validated, immutably-versioned
farm/field rows. This is the only writer of farm + field geometry on the remote-sense side
(CLAUDE.md invariant 6 - no field is written by both systems).

What it enforces:
  * geometry validity + normalisation to WGS84 (geo.validate_geometry);
  * CRS → working UTM by centroid (DI-2);
  * field-within-farm nesting to a survey tolerance (DI-3);
  * a farm with no fields gets one field derived from its boundary (DI-4);
  * idempotent upsert: re-sending an identical payload changes nothing;
  * geometry versioning: a genuinely changed boundary bumps the version, archives the prior
    boundary, and flags the field for fresh backfill while retaining old analyses (DI-5).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import from_shape, to_shape
from rs_core.db import get_session
from rs_core.geo import (
    GeometryValidation,
    area_m2,
    geometries_equivalent,
    is_nested,
    nesting_fraction_outside,
    parse_epsg,
    utm_epsg_for,
    validate_geometry,
)
from rs_core.logging import get_logger
from rs_core.models import Farm, Field, FieldGeometryVersion
from rs_core.schemas import (
    FarmIn,
    FarmIngestReport,
    FieldIn,
    FieldIngestReport,
)
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("ingestion")

router = APIRouter(prefix="/ingest", tags=["ingestion"])


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


async def _fetch_field(
    session: AsyncSession, *, farm_id: uuid.UUID, canonical_field_id: str
) -> Field | None:
    return (
        await session.execute(
            select(Field).where(
                Field.farm_id == farm_id,
                Field.canonical_field_id == canonical_field_id,
            )
        )
    ).scalar_one_or_none()


async def get_or_create_field(
    session: AsyncSession,
    *,
    build: Callable[[], Field],
    farm_id: uuid.UUID,
    canonical_field_id: str | None,
) -> tuple[Field, bool]:
    """Create a field, idempotent under a concurrent first-create on (farm, canonical_field_id)
    (D10). Returns (field, created). A keyed field is inserted inside a savepoint; on the unique
    violation (uq_field_farm_canonical) we roll back, re-fetch the winner and return it as
    not-created. An unkeyed/derived field has no unique key to dedupe on, so it is inserted
    directly - its concurrency belongs to the broader R-1 enqueue work, tracked separately."""
    field = build()
    if canonical_field_id is None:
        session.add(field)
        await session.flush()
        return field, True
    try:
        async with session.begin_nested():
            session.add(field)
            await session.flush()
    except IntegrityError:
        winner = await _fetch_field(session, farm_id=farm_id, canonical_field_id=canonical_field_id)
        if winner is None:
            raise
        return winner, False
    return field, True


async def _upsert_field(
    session: AsyncSession,
    *,
    farm: Farm,
    existing: Field | None,
    geom_wgs84: BaseGeometry,
    canonical_field_id: str | None,
    name: str | None,
    crop: str | None,
    src_epsg: int,
    working_crs: str,
    derived_from_farm: bool,
    repaired: bool,
    warnings: list[str],
) -> FieldIngestReport:
    stored = _as_multipolygon(geom_wgs84)
    now = datetime.now(UTC)

    if existing is None:

        def _build_field() -> Field:
            return Field(
                farm_id=farm.id,
                canonical_field_id=canonical_field_id,
                name=name,
                crop=crop,
                boundary=from_shape(stored, srid=4326),
                geometry_version=1,
                derived_from_farm=derived_from_farm,
                needs_backfill=True,
                source_crs=_crs_str(src_epsg),
                working_crs=working_crs,
            )

        new_field, created = await get_or_create_field(
            session,
            build=_build_field,
            farm_id=farm.id,
            canonical_field_id=canonical_field_id,
        )
        if not created:
            # Lost a concurrent first-create (D10): a competitor inserted this field first. It is
            # the same arrival payload, so the result converges - report unchanged at the winner's
            # version instead of 500-ing on the unique violation.
            return FieldIngestReport(
                canonical_field_id=canonical_field_id,
                field_id=str(new_field.id),
                action="unchanged",
                geometry_version=new_field.geometry_version,
                repaired=repaired,
                warnings=warnings,
            )
        session.add(
            FieldGeometryVersion(
                field_id=new_field.id,
                version=1,
                boundary=from_shape(stored, srid=4326),
                area_m2=area_m2(geom_wgs84),
                valid_from=now,
            )
        )
        action = "derived_from_farm" if derived_from_farm else "created"
        return FieldIngestReport(
            canonical_field_id=canonical_field_id,
            field_id=str(new_field.id),
            action=action,
            geometry_version=1,
            repaired=repaired,
            warnings=warnings,
        )

    # Refresh mutable identity fields regardless of geometry change.
    existing.name = name
    existing.crop = crop

    current_geom = to_shape(existing.boundary)
    if geometries_equivalent(current_geom, geom_wgs84):
        return FieldIngestReport(
            canonical_field_id=canonical_field_id,
            field_id=str(existing.id),
            action="unchanged",
            geometry_version=existing.geometry_version,
            repaired=repaired,
            warnings=warnings,
        )

    # Genuine boundary change (DI-5): archive the current version, bump, flag for backfill.
    current_version = (
        await session.execute(
            select(FieldGeometryVersion).where(
                FieldGeometryVersion.field_id == existing.id,
                FieldGeometryVersion.version == existing.geometry_version,
            )
        )
    ).scalar_one_or_none()
    if current_version is not None and current_version.valid_to is None:
        current_version.valid_to = now

    existing.geometry_version += 1
    existing.boundary = from_shape(stored, srid=4326)
    existing.needs_backfill = True
    session.add(
        FieldGeometryVersion(
            field_id=existing.id,
            version=existing.geometry_version,
            boundary=from_shape(stored, srid=4326),
            area_m2=area_m2(geom_wgs84),
            valid_from=now,
        )
    )
    return FieldIngestReport(
        canonical_field_id=canonical_field_id,
        field_id=str(existing.id),
        action="updated_geometry",
        geometry_version=existing.geometry_version,
        repaired=repaired,
        warnings=warnings,
    )


def _match_existing(
    incoming_canonical: str | None,
    incoming_geom: BaseGeometry,
    by_canonical: dict[str, Field],
    unkeyed: list[Field],
) -> Field | None:
    """Find the existing field an incoming one refers to. Prefer the canonical id; fall back
    to spatial equality for fields the gateway sends without an id (keeps re-sends idempotent
    rather than duplicating)."""
    if incoming_canonical is not None:
        return by_canonical.get(incoming_canonical)
    for candidate in unkeyed:
        if geometries_equivalent(to_shape(candidate.boundary), incoming_geom):
            return candidate
    return None


async def _fetch_farm(session: AsyncSession, canonical_farm_id: str) -> Farm | None:
    return (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()


async def get_or_create_farm(
    session: AsyncSession, *, canonical_farm_id: str, build: Callable[[], Farm]
) -> tuple[Farm, bool]:
    """Resolve the farm row for an ingest, idempotent under a concurrent first-create (D8/R-1).
    Returns (farm, created). If the farm is absent we insert it inside a savepoint; if a competing
    request created it first (unique violation on canonical_farm_id), we roll the savepoint back,
    re-fetch the winner and return it as not-created - so two simultaneous first ingests of the
    same farm both succeed instead of one failing with a 500."""
    existing = await _fetch_farm(session, canonical_farm_id)
    if existing is not None:
        return existing, False
    farm = build()
    try:
        async with session.begin_nested():
            session.add(farm)
            await session.flush()
    except IntegrityError:
        winner = await _fetch_farm(session, canonical_farm_id)
        if winner is None:
            raise
        return winner, False
    return farm, True


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
