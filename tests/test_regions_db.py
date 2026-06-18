"""PostGIS-backed region foundation (PRD 0002 slice 1): the idempotent read-only seed with its
trivial composition, centroid assignment with layer-version stamping, recompute idempotency, and
stale-assignment removal when a farm moves outside every region. SKIPS when no database is reachable
(prior art test_collection_state_db.py); runs for real in CI / `docker compose`."""

from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, FarmRegionAssignment, RegionBoundary, RegionBoundaryLayer
from rs_core.regions import ParsedRegionFeature
from rs_core.repositories.regions import recompute_farm_region_assignments, seed_natural_regions
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.workspace.regions import (
    create_drawn_region_from_geojson,
    ingest_region_upload,
)

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_TOL = 250.0


def _rect_mp(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> MultiPolygon:
    return MultiPolygon(
        [
            Polygon(
                [
                    (min_lon, min_lat),
                    (max_lon, min_lat),
                    (max_lon, max_lat),
                    (min_lon, max_lat),
                    (min_lon, min_lat),
                ]
            )
        ]
    )


def _features() -> list[ParsedRegionFeature]:
    # A: lon 29..31, B: lon 31..33 (both lat -18..-16). Synthetic, not real zones.
    return [
        ParsedRegionFeature("Region A", _rect_mp(29.0, -18.0, 31.0, -16.0), "EPSG:4326"),
        ParsedRegionFeature("Region B", _rect_mp(31.0, -18.0, 33.0, -16.0), "EPSG:4326"),
    ]


@pytest_asyncio.fixture
async def maker_():
    engine = create_async_engine(_TEST_DB_URL, connect_args={"connect_timeout": 2})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # no DB reachable -> skip the whole DB-backed suite
        await engine.dispose()
        pytest.skip(f"PostGIS not reachable at {_TEST_DB_URL}: {exc}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(maker, *, version: str = "v1"):
    async with maker() as session:
        layer = await seed_natural_regions(
            session,
            name="Zimbabwe NR (test)",
            source="test-source",
            year=2020,
            version=version,
            crs="EPSG:4326",
            features=_features(),
        )
        await session.commit()
        return layer.id


async def _add_farm(maker, cfid: str, lon: float, lat: float) -> None:
    async with maker() as session:
        session.add(
            Farm(
                canonical_farm_id=cfid,
                boundary=from_shape(
                    _rect_mp(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01), srid=4326
                ),
                centroid_lon=lon,
                centroid_lat=lat,
                source_crs="EPSG:4326",
                working_crs="EPSG:32736",
            )
        )
        await session.commit()


async def _count(maker, model) -> int:
    async with maker() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seed_creates_readonly_layer_with_trivial_composition(maker_) -> None:
    await _seed(maker_)
    async with maker_() as session:
        layer = (await session.execute(select(RegionBoundaryLayer))).scalar_one()
        assert layer.read_only is True
        boundaries = (await session.execute(select(RegionBoundary))).scalars().all()
        assert {b.name for b in boundaries} == {"Region A", "Region B"}
        for b in boundaries:
            assert b.source == "seeded"
            assert b.nr_composition == {b.name: 1.0}
            assert b.dominant_nr == b.name


async def test_seed_is_idempotent(maker_) -> None:
    await _seed(maker_)
    await _seed(maker_)  # same (source, year, version) identity -> no-op
    assert await _count(maker_, RegionBoundaryLayer) == 1
    assert await _count(maker_, RegionBoundary) == 2


async def test_recompute_assigns_and_stamps_version(maker_) -> None:
    await _seed(maker_, version="v1")
    await _add_farm(maker_, "FARM-A", 30.0, -17.0)  # inside Region A
    async with maker_() as session:
        written = await recompute_farm_region_assignments(session, edge_tolerance_m=_TOL)
        await session.commit()
        assert written == 1
    async with maker_() as session:
        row = (await session.execute(select(FarmRegionAssignment))).scalar_one()
        assert row.canonical_farm_id == "FARM-A"
        assert row.layer_version == "v1"
        region = (
            await session.execute(
                select(RegionBoundary).where(RegionBoundary.id == row.region_boundary_id)
            )
        ).scalar_one()
        assert region.name == "Region A"


async def test_recompute_is_idempotent(maker_) -> None:
    await _seed(maker_)
    await _add_farm(maker_, "FARM-A", 30.0, -17.0)
    for _ in range(2):
        async with maker_() as session:
            await recompute_farm_region_assignments(session, edge_tolerance_m=_TOL)
            await session.commit()
    assert await _count(maker_, FarmRegionAssignment) == 1


async def test_recompute_removes_stale_assignment_when_farm_moves_out(maker_) -> None:
    await _seed(maker_)
    await _add_farm(maker_, "FARM-M", 30.0, -17.0)
    async with maker_() as session:
        await recompute_farm_region_assignments(session, edge_tolerance_m=_TOL)
        await session.commit()
    assert await _count(maker_, FarmRegionAssignment) == 1

    async with maker_() as session:  # move the farm outside every region
        farm = (
            await session.execute(select(Farm).where(Farm.canonical_farm_id == "FARM-M"))
        ).scalar_one()
        farm.centroid_lon = 40.0
        farm.centroid_lat = -17.0
        await session.commit()
    async with maker_() as session:
        written = await recompute_farm_region_assignments(
            session, canonical_farm_id="FARM-M", edge_tolerance_m=_TOL
        )
        await session.commit()
        assert written == 0
    assert await _count(maker_, FarmRegionAssignment) == 0


async def test_create_drawn_region_captures_farm_and_composes_nr(maker_) -> None:
    await _seed(maker_)  # Region A lon 29..31, Region B lon 31..33
    await _add_farm(maker_, "FARM-D", 30.0, -17.0)  # inside Region A and the drawn region
    drawn = {
        "type": "Polygon",
        "coordinates": [
            [[29.5, -17.5], [30.5, -17.5], [30.5, -16.5], [29.5, -16.5], [29.5, -17.5]]
        ],
    }
    async with maker_() as session:
        boundary, captured = await create_drawn_region_from_geojson(
            session, name="My Area", geometry=drawn, creator="analyst-1", edge_tolerance_m=_TOL
        )
        await session.commit()
        layer_id = boundary.layer_id
        assert boundary.source == "drawn"
        assert boundary.creator == "analyst-1"
        assert boundary.dominant_nr == "Region A"  # fully inside Region A
        assert boundary.nr_composition == {"Region A": 1.0}
        assert captured == 1  # FARM-D falls inside the drawn region
    async with maker_() as session:
        layer = (
            await session.execute(
                select(RegionBoundaryLayer).where(RegionBoundaryLayer.id == layer_id)
            )
        ).scalar_one()
        assert layer.read_only is False  # analyst-created, not the protected seeded layer


def _ward_feature(name: str, coords: list) -> dict:
    return {
        "type": "Feature",
        "properties": {"ward": name},
        "geometry": {"type": "Polygon", "coordinates": coords},
    }


async def test_upload_creates_regions_and_skips_broken(maker_) -> None:
    pytest.importorskip("geopandas")
    await _seed(maker_)
    await _add_farm(maker_, "FARM-U", 30.0, -17.0)  # inside Ward 1
    ward1 = [[[29.5, -17.5], [30.5, -17.5], [30.5, -16.5], [29.5, -16.5], [29.5, -17.5]]]
    ward2 = [[[31.5, -17.5], [32.5, -17.5], [32.5, -16.5], [31.5, -16.5], [31.5, -17.5]]]
    tiny = [
        [
            [30.0, -17.0],
            [30.000001, -17.0],
            [30.000001, -17.000001],
            [30.0, -17.000001],
            [30.0, -17.0],
        ]
    ]
    fc = {
        "type": "FeatureCollection",
        "features": [
            _ward_feature("Ward 1", ward1),
            _ward_feature("Ward 2", ward2),
            _ward_feature("Broken", tiny),  # degenerate area -> skipped, not fatal
        ],
    }
    raw = json.dumps(fc).encode()
    async with maker_() as session:
        layer, created, skipped, assigned = await ingest_region_upload(
            session,
            raw=raw,
            filename="wards.geojson",
            name_column="ward",
            creator="admin-1",
            edge_tolerance_m=_TOL,
        )
        await session.commit()
        layer_id = layer.id
        assert created == 2
        assert len(skipped) == 1
        assert assigned == 1  # FARM-U inside Ward 1
    async with maker_() as session:
        boundaries = (
            (
                await session.execute(
                    select(RegionBoundary).where(RegionBoundary.layer_id == layer_id)
                )
            )
            .scalars()
            .all()
        )
        assert {b.name for b in boundaries} == {"Ward 1", "Ward 2"}
        assert all(b.source == "uploaded" and b.creator == "admin-1" for b in boundaries)
