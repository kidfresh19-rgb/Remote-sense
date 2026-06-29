"""Region clusters: the seeded/uploaded/drawn boundary layers and each farm's centroid assignment
(comparison groups, PRD 0002 / ADR 0010). Seeding is idempotent on the layer identity so re-running
init - or replacing a candidate map with the authoritative one - never duplicates. Assignment is the
one centroid point-in-polygon rule for every boundary `source`, stamped with the layer version so a
re-survey or a boundary edit is a tracked re-assignment (the recompute upserts), never a silent
overwrite. Reference geometry is owned by remote-sense and never pushed (invariant 6)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from geoalchemy2.shape import from_shape
from geoalchemy2.shape import to_shape as wkb_to_shape
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import (
    Farm,
    FarmRegionAssignment,
    Household,
    Plot,
    RegionBoundary,
    RegionBoundaryLayer,
)
from rs_core.regions import (
    ParsedRegionFeature,
    RegionSource,
    assign_centroid,
    natural_region_composition,
)


async def get_layer_by_identity(
    session: AsyncSession, *, source: str, year: int | None, version: str
) -> RegionBoundaryLayer | None:
    """The layer matching a published-map identity, or None. Backs the idempotent seed check."""
    return (
        await session.execute(
            select(RegionBoundaryLayer).where(
                RegionBoundaryLayer.source == source,
                RegionBoundaryLayer.year == year,
                RegionBoundaryLayer.version == version,
            )
        )
    ).scalar_one_or_none()


async def list_layers(session: AsyncSession) -> Sequence[RegionBoundaryLayer]:
    """Every region-boundary layer, seeded and analyst-created."""
    return (await session.execute(select(RegionBoundaryLayer))).scalars().all()


async def get_region_layer(
    session: AsyncSession, *, layer_id: uuid.UUID
) -> RegionBoundaryLayer | None:
    """One layer by id, or None. Lets a read endpoint tell an unknown layer (404) apart from a
    layer that exists but holds no boundaries (an empty collection)."""
    return (
        await session.execute(select(RegionBoundaryLayer).where(RegionBoundaryLayer.id == layer_id))
    ).scalar_one_or_none()


async def list_region_layers_with_counts(
    session: AsyncSession,
) -> list[tuple[RegionBoundaryLayer, int]]:
    """Every region-boundary layer with its boundary count, newest first. Backs the workspace map's
    layer toggle (PRD 0002 slices 8a/8b): the seeded Natural Region layer and any analyst-uploaded
    layers. Counting here means the frontend never fetches every boundary just to label a layer."""
    rows = (
        await session.execute(
            select(RegionBoundaryLayer, func.count(RegionBoundary.id))
            .outerjoin(RegionBoundary, RegionBoundary.layer_id == RegionBoundaryLayer.id)
            .group_by(RegionBoundaryLayer.id)
            .order_by(RegionBoundaryLayer.created_at.desc())
        )
    ).all()
    return [(layer, count) for layer, count in rows]


async def region_boundaries_for_layer(
    session: AsyncSession, *, layer_id: uuid.UUID
) -> Sequence[RegionBoundary]:
    """One layer's boundaries, name-ordered for a stable map draw order. Returns [] for an unknown
    or empty layer; the endpoint uses `get_region_layer` to distinguish the two."""
    return (
        (
            await session.execute(
                select(RegionBoundary)
                .where(RegionBoundary.layer_id == layer_id)
                .order_by(RegionBoundary.name)
            )
        )
        .scalars()
        .all()
    )


async def seed_natural_regions(
    session: AsyncSession,
    *,
    name: str,
    source: str,
    year: int | None,
    version: str,
    crs: str,
    features: Sequence[ParsedRegionFeature],
    publishing_authority: str | None = None,
    citation: str | None = None,
    naming_column: str | None = None,
    acquisition_date: date | None = None,
    acquisition_path: str | None = None,
    file_path: str | None = None,
) -> RegionBoundaryLayer:
    """Seed a read-only Natural Region layer from validated features. Idempotent on
    (source, year, version): an existing layer is returned untouched, so re-running init is
    a no-op and the authoritative map can supersede a candidate by carrying a new version. Each
    seeded boundary is `source=seeded` with the trivial self-composition ({name: 1.0}, dominant_nr =
    the region itself), because the seeded layer *is* the Natural Region reference."""
    existing = await get_layer_by_identity(session, source=source, year=year, version=version)
    if existing is not None:
        return existing

    layer = RegionBoundaryLayer(
        name=name,
        source=source,
        year=year,
        version=version,
        publishing_authority=publishing_authority,
        citation=citation,
        naming_column=naming_column,
        crs=crs,
        acquisition_date=acquisition_date,
        acquisition_path=acquisition_path,
        file_path=file_path,
        read_only=True,
    )
    session.add(layer)
    await session.flush()  # assign layer.id before the boundaries reference it

    for feature in features:
        session.add(
            RegionBoundary(
                layer_id=layer.id,
                name=feature.name,
                boundary=from_shape(feature.geometry, srid=4326),
                source=RegionSource.SEEDED.value,
                creator=None,
                nr_composition={feature.name: 1.0},
                dominant_nr=feature.name,
            )
        )
    await session.flush()
    return layer


async def _layer_candidates(
    session: AsyncSession, *, layer_id: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, str, list[tuple[uuid.UUID, BaseGeometry]]]]:
    """Every layer (or one, when `layer_id` is given) as (layer_id, layer_version,
    [(region_boundary_id, WGS84 polygon), ...]), the shape `assign_centroid` consumes. Read once per
    recompute so a many-farm sweep does not re-query boundaries per farm."""
    out: list[tuple[uuid.UUID, str, list[tuple[uuid.UUID, BaseGeometry]]]] = []
    stmt = select(RegionBoundaryLayer)
    if layer_id is not None:
        stmt = stmt.where(RegionBoundaryLayer.id == layer_id)
    layers = (await session.execute(stmt)).scalars().all()
    for layer in layers:
        boundaries = (
            (
                await session.execute(
                    select(RegionBoundary).where(RegionBoundary.layer_id == layer.id)
                )
            )
            .scalars()
            .all()
        )
        candidates = [(b.id, wkb_to_shape(b.boundary)) for b in boundaries]
        out.append((layer.id, layer.version, candidates))
    return out


async def recompute_farm_region_assignments(
    session: AsyncSession,
    *,
    canonical_farm_id: str | None = None,
    layer_id: uuid.UUID | None = None,
    edge_tolerance_m: float,
) -> int:
    """Assign each farm to the region containing its centroid, per layer. `canonical_farm_id`
    set: one farm (on register or geometry-version change). None: every farm (after a seed or a
    boundary edit). `layer_id` set scopes the sweep to one layer (after a create), so a new drawn or
    uploaded region assigns its captured farms without re-walking other layers. Idempotent - the
    assignment is upserted on (canonical_farm_id, layer_id) and
    stamped with the layer version, and a farm now outside a layer has its stale assignment
    removed - re-running changes nothing. Returns the number of (farm, layer) assignments written.

    The boundary-adjacent flag and all distance work happen in the working UTM zone inside
    `assign_centroid` (§2). No geometry leaves remote-sense (invariant 6)."""
    layer_candidates = await _layer_candidates(session, layer_id=layer_id)
    if not layer_candidates:
        return 0

    farm_stmt = select(Farm.canonical_farm_id, Farm.centroid_lon, Farm.centroid_lat)
    if canonical_farm_id is not None:
        farm_stmt = farm_stmt.where(Farm.canonical_farm_id == canonical_farm_id)
    farms = (await session.execute(farm_stmt)).all()

    written = 0
    for farm_cid, lon, lat in farms:
        for layer_id, layer_version, candidates in layer_candidates:
            assignment = assign_centroid(lon, lat, candidates, edge_tolerance_m=edge_tolerance_m)
            if assignment is None:
                await session.execute(
                    delete(FarmRegionAssignment).where(
                        FarmRegionAssignment.canonical_farm_id == farm_cid,
                        FarmRegionAssignment.layer_id == layer_id,
                    )
                )
                continue
            await session.execute(
                pg_insert(FarmRegionAssignment)
                .values(
                    canonical_farm_id=farm_cid,
                    layer_id=layer_id,
                    region_boundary_id=assignment.region_boundary_id,
                    layer_version=layer_version,
                    boundary_adjacent=assignment.boundary_adjacent,
                )
                .on_conflict_do_update(
                    constraint="uq_farm_region_assignment",
                    set_={
                        "region_boundary_id": assignment.region_boundary_id,
                        "layer_version": layer_version,
                        "boundary_adjacent": assignment.boundary_adjacent,
                        "assigned_at": func.now(),
                    },
                )
            )
            written += 1
    await session.flush()
    return written


async def get_assignments_for_farm(
    session: AsyncSession, *, canonical_farm_id: str
) -> Sequence[FarmRegionAssignment]:
    """A farm's region assignments, one per layer it falls inside."""
    return (
        (
            await session.execute(
                select(FarmRegionAssignment).where(
                    FarmRegionAssignment.canonical_farm_id == canonical_farm_id
                )
            )
        )
        .scalars()
        .all()
    )


async def natural_region_polygons(session: AsyncSession) -> list[tuple[str, BaseGeometry]]:
    """The seeded Natural Region polygons (name, WGS84 shapely) used to derive a created boundary's
    composition. Reads the most recent read-only (seeded) layer; returns [] when none is seeded yet,
    so composition degrades gracefully to ({}, None)."""
    layer = (
        (
            await session.execute(
                select(RegionBoundaryLayer)
                .where(RegionBoundaryLayer.read_only.is_(True))
                .order_by(RegionBoundaryLayer.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if layer is None:
        return []
    boundaries = (
        (await session.execute(select(RegionBoundary).where(RegionBoundary.layer_id == layer.id)))
        .scalars()
        .all()
    )
    return [(b.name, wkb_to_shape(b.boundary)) for b in boundaries]


async def create_drawn_region(
    session: AsyncSession,
    *,
    name: str,
    geometry: MultiPolygon,
    creator: str | None,
    nr_polygons: Sequence[tuple[str, BaseGeometry]],
) -> RegionBoundary:
    """Persist one analyst-drawn region as its own free-standing layer (owned by no farm), tagged
    `source=drawn` with its creator and the NR composition + dominant NR. Each drawn region is
    its own layer so a farm can belong to several overlapping drawn regions at once (one assignment
    per layer). The version is a fresh token, so creating two regions never collides on the layer
    identity (unlike the idempotent seed)."""
    composition, dominant = natural_region_composition(geometry, nr_polygons)
    layer = RegionBoundaryLayer(
        name=name,
        source="analyst-draw",
        year=None,
        version=uuid.uuid4().hex,
        crs="EPSG:4326",
        read_only=False,
    )
    session.add(layer)
    await session.flush()
    boundary = RegionBoundary(
        layer_id=layer.id,
        name=name,
        boundary=from_shape(geometry, srid=4326),
        source=RegionSource.DRAWN.value,
        creator=creator,
        nr_composition=composition,
        dominant_nr=dominant,
    )
    session.add(boundary)
    await session.flush()
    return boundary


async def create_uploaded_layer(
    session: AsyncSession,
    *,
    name: str,
    features: Sequence[ParsedRegionFeature],
    creator: str | None,
    nr_polygons: Sequence[tuple[str, BaseGeometry]],
) -> RegionBoundaryLayer:
    """Persist a multi-feature upload as one layer of `source=uploaded` regions in a single
    transaction (a single-feature file is the natural one-region case). Each boundary carries its
    creator and derived NR composition + dominant NR. The caller has already validated each feature
    and skip-reported broken ones via `read_region_layer`."""
    source_crs = features[0].source_crs if features else "EPSG:4326"
    layer = RegionBoundaryLayer(
        name=name,
        source="analyst-upload",
        year=None,
        version=uuid.uuid4().hex,
        crs=source_crs,
        read_only=False,
    )
    session.add(layer)
    await session.flush()
    for feature in features:
        composition, dominant = natural_region_composition(feature.geometry, nr_polygons)
        session.add(
            RegionBoundary(
                layer_id=layer.id,
                name=feature.name,
                boundary=from_shape(feature.geometry, srid=4326),
                source=RegionSource.UPLOADED.value,
                creator=creator,
                nr_composition=composition,
                dominant_nr=dominant,
            )
        )
    await session.flush()
    return layer


async def seed_ward_boundaries(
    session: AsyncSession,
    *,
    name: str,
    source: str,
    year: int | None,
    version: str,
    crs: str,
    features: Sequence[ParsedRegionFeature],
    publishing_authority: str | None = None,
    citation: str | None = None,
    naming_column: str | None = None,
    acquisition_date: date | None = None,
    acquisition_path: str | None = None,
    file_path: str | None = None,
) -> RegionBoundaryLayer:
    """Seed a read-only ward administrative boundary layer. Idempotent on (source, year, version):
    re-running with the same provenance is a no-op; a new version string supersedes the candidate.
    Ward boundaries do not carry NR composition (they are a different administrative layer); the
    Household.ward_boundary_id FK links a household to its ward boundary after assignment."""
    existing = await get_layer_by_identity(session, source=source, year=year, version=version)
    if existing is not None:
        return existing

    layer = RegionBoundaryLayer(
        name=name,
        source=source,
        year=year,
        version=version,
        publishing_authority=publishing_authority,
        citation=citation,
        naming_column=naming_column,
        crs=crs,
        acquisition_date=acquisition_date,
        acquisition_path=acquisition_path,
        file_path=file_path,
        read_only=True,
    )
    session.add(layer)
    await session.flush()

    for feature in features:
        session.add(
            RegionBoundary(
                layer_id=layer.id,
                name=feature.name,
                boundary=from_shape(feature.geometry, srid=4326),
                source=RegionSource.SEEDED.value,
                creator=None,
                nr_composition={},
                dominant_nr=None,
            )
        )
    await session.flush()
    return layer


async def assign_households_to_ward_by_name(
    session: AsyncSession,
    *,
    layer_id: uuid.UUID,
    household_id: uuid.UUID | None = None,
) -> int:
    """Assign households to their ward boundary by matching Household.ward_name to
    RegionBoundary.name (case-insensitive) within `layer_id`. `household_id` set: one household;
    None: every household with a non-null ward_name. Returns the number of assignments written.

    Name-match is the reliable path in the communal context: officers declare their ward at
    enrollment (PRD 0003 §8). Centroid-containment is deferred to after 0031 (plot analysis)
    provides plot centroids for every household."""
    boundaries = (
        await session.execute(
            select(RegionBoundary.id, RegionBoundary.name).where(
                RegionBoundary.layer_id == layer_id
            )
        )
    ).all()
    name_to_id: dict[str, uuid.UUID] = {b.name.lower(): b.id for b in boundaries}
    if not name_to_id:
        return 0

    stmt = select(Household).where(Household.ward_name.is_not(None))
    if household_id is not None:
        stmt = stmt.where(Household.id == household_id)
    households = (await session.execute(stmt)).scalars().all()

    written = 0
    for household in households:
        if household.ward_name is None:
            continue
        boundary_id = name_to_id.get(household.ward_name.lower())
        if boundary_id is None:
            continue
        household.ward_boundary_id = boundary_id
        written += 1

    await session.flush()
    return written


async def _boundary_candidates_with_names(
    session: AsyncSession, layer_id: uuid.UUID
) -> tuple[list[tuple[uuid.UUID, BaseGeometry]], dict[uuid.UUID, str]]:
    """One layer's boundaries as the `(boundary_id, WGS84 polygon)` list `assign_centroid` consumes,
    plus a `{boundary_id: name}` map so a hit can be turned back into the ward / NR name."""
    boundaries = (
        (await session.execute(select(RegionBoundary).where(RegionBoundary.layer_id == layer_id)))
        .scalars()
        .all()
    )
    candidates = [(b.id, wkb_to_shape(b.boundary)) for b in boundaries]
    names = {b.id: b.name for b in boundaries}
    return candidates, names


async def _household_centroids(
    session: AsyncSession, household_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[float, float]]:
    """Representative `(lon, lat)` per household: the centroid of the union of its plot geometries.
    A household with no plot geometry yet is absent from the map (nothing to place on)."""
    rows = (
        await session.execute(
            select(Plot.household_id, Plot.boundary).where(Plot.household_id.in_(household_ids))
        )
    ).all()
    geoms: dict[uuid.UUID, list[BaseGeometry]] = defaultdict(list)
    for household_id, boundary in rows:
        if boundary is not None:
            geoms[household_id].append(wkb_to_shape(boundary))
    centroids: dict[uuid.UUID, tuple[float, float]] = {}
    for household_id, plot_geoms in geoms.items():
        point = unary_union(plot_geoms).centroid
        centroids[household_id] = (point.x, point.y)
    return centroids


async def assign_households_by_centroid(
    session: AsyncSession,
    *,
    ward_layer_id: uuid.UUID,
    nr_layer_id: uuid.UUID,
    edge_tolerance_m: float,
    household_id: uuid.UUID | None = None,
) -> int:
    """Assign households to their ward (administrative layer) and Natural Region (AEZ layer) by the
    centroid of the union of their plot geometries - the centroid-containment path 0027 deferred
    until 0031 materialised plot geometry. Sets `Household.ward_boundary_id` (and `ward_name` only
    when it was unset, never clobbering an officer's enrollment declaration) and
    `Household.dominant_nr` (for the 0032 cohort key). A household with no plot geometry is skipped.
    Returns the number assigned. All point-in-polygon and distance work happens in the working UTM
    zone inside `assign_centroid` (§2). No geometry leaves remote-sense (invariant 6)."""
    ward_candidates, ward_names = await _boundary_candidates_with_names(session, ward_layer_id)
    nr_candidates, nr_names = await _boundary_candidates_with_names(session, nr_layer_id)

    stmt = select(Household)
    if household_id is not None:
        stmt = stmt.where(Household.id == household_id)
    households = (await session.execute(stmt)).scalars().all()
    if not households:
        return 0
    centroids = await _household_centroids(session, [h.id for h in households])

    written = 0
    for household in households:
        centroid = centroids.get(household.id)
        if centroid is None:
            continue
        lon, lat = centroid
        ward = assign_centroid(lon, lat, ward_candidates, edge_tolerance_m=edge_tolerance_m)
        if ward is not None:
            household.ward_boundary_id = ward.region_boundary_id
            if household.ward_name is None:
                household.ward_name = ward_names.get(ward.region_boundary_id)
        nr = assign_centroid(lon, lat, nr_candidates, edge_tolerance_m=edge_tolerance_m)
        household.dominant_nr = nr_names.get(nr.region_boundary_id) if nr is not None else None
        written += 1

    await session.flush()
    return written
