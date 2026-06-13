"""Ingestion persistence: concurrency-safe get-or-create for farms and keyed fields (D8/D10,
savepoint + unique-violation fallback), the field upsert with immutable geometry versioning
(DI-5), and the canonical-id / spatial-equality matcher that keeps re-sends idempotent."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from geoalchemy2.shape import from_shape, to_shape
from rs_core.geo import area_m2, geometries_equivalent
from rs_core.models import Farm, Field, FieldGeometryVersion
from rs_core.schemas import FieldIngestReport
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.ingestion.validation import _as_multipolygon, _crs_str


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
