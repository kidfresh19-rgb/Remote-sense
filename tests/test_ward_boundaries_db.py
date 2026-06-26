"""Ward Watch ward boundary seed + household assignment against PostGIS (backlog 0027).
SKIPS when no database is reachable; runs for real in CI / docker compose."""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from rs_core.db import Base
from rs_core.models import Household
from rs_core.regions import ParsedRegionFeature
from rs_core.repositories.regions import (
    assign_households_to_ward_by_name,
    seed_ward_boundaries,
)
from shapely.geometry import MultiPolygon, Polygon, box
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)


def _square_mp(lon: float, lat: float, side: float = 0.01) -> MultiPolygon:
    h = side / 2
    poly = Polygon(
        [
            (lon - h, lat - h),
            (lon + h, lat - h),
            (lon + h, lat + h),
            (lon - h, lat + h),
            (lon - h, lat - h),
        ]
    )
    return MultiPolygon([poly])


@pytest_asyncio.fixture
async def maker_():
    engine = create_async_engine(_TEST_DB_URL, connect_args={"connect_timeout": 2})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        pytest.skip(f"PostGIS not reachable at {_TEST_DB_URL}: {exc}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session_(maker_):
    async with maker_() as s:
        yield s


@pytest.mark.asyncio
async def test_seed_ward_boundaries_idempotent(maker_):
    """Seeding the same (source, year, version) twice returns the existing layer."""
    features = [
        ParsedRegionFeature(
            name="Makoni Ward 5",
            geometry=box(31.5, -18.5, 31.7, -18.3),
            source_crs="EPSG:4326",
        )
    ]
    async with maker_() as session:
        layer1 = await seed_ward_boundaries(
            session,
            name="Test Ward Layer",
            source="test-idem-source",
            year=2023,
            version="test-idem-v1",
            crs="EPSG:4326",
            features=features,
        )
        layer2 = await seed_ward_boundaries(
            session,
            name="Test Ward Layer",
            source="test-idem-source",
            year=2023,
            version="test-idem-v1",
            crs="EPSG:4326",
            features=features,
        )
        assert layer1.id == layer2.id


@pytest.mark.asyncio
async def test_seed_ward_boundaries_read_only(maker_):
    async with maker_() as session:
        layer = await seed_ward_boundaries(
            session,
            name="Test Ward Layer",
            source="test-ro-source",
            year=2023,
            version="test-ro-v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature(
                    name="Ward A",
                    geometry=box(31.5, -18.5, 31.7, -18.3),
                    source_crs="EPSG:4326",
                )
            ],
        )
        assert layer.read_only is True


@pytest.mark.asyncio
async def test_assign_household_by_matching_ward_name(maker_):
    """A household with ward_name matching a seeded boundary gets its FK set."""
    async with maker_() as session:
        layer = await seed_ward_boundaries(
            session,
            name="Test Ward Layer",
            source="test-assign-source",
            year=2023,
            version="test-assign-v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature(
                    name="Makoni Ward 5",
                    geometry=box(31.5, -18.5, 31.7, -18.3),
                    source_crs="EPSG:4326",
                )
            ],
        )
        hh = Household(client_uuid=uuid.uuid4(), ward_name="Makoni Ward 5")
        session.add(hh)
        await session.flush()

        assigned = await assign_households_to_ward_by_name(session, layer_id=layer.id)
        assert assigned == 1
        await session.refresh(hh)
        assert hh.ward_boundary_id is not None


@pytest.mark.asyncio
async def test_assign_unmatched_ward_name_stays_null(maker_):
    async with maker_() as session:
        layer = await seed_ward_boundaries(
            session,
            name="Test Ward Layer",
            source="test-nomatch-source",
            year=2023,
            version="test-nomatch-v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature(
                    name="Bindura Ward 2",
                    geometry=box(30.9, -17.4, 31.1, -17.2),
                    source_crs="EPSG:4326",
                )
            ],
        )
        hh = Household(client_uuid=uuid.uuid4(), ward_name="Unknown Ward")
        session.add(hh)
        await session.flush()

        assigned = await assign_households_to_ward_by_name(session, layer_id=layer.id)
        assert assigned == 0
        await session.refresh(hh)
        assert hh.ward_boundary_id is None


@pytest.mark.asyncio
async def test_assign_case_insensitive_ward_name(maker_):
    async with maker_() as session:
        layer = await seed_ward_boundaries(
            session,
            name="Test Ward Layer",
            source="test-case-source",
            year=2023,
            version="test-case-v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature(
                    name="Chegutu Ward 7",
                    geometry=box(29.8, -18.2, 30.0, -18.0),
                    source_crs="EPSG:4326",
                )
            ],
        )
        hh = Household(client_uuid=uuid.uuid4(), ward_name="chegutu ward 7")
        session.add(hh)
        await session.flush()

        assigned = await assign_households_to_ward_by_name(session, layer_id=layer.id)
        assert assigned == 1
        await session.refresh(hh)
        assert hh.ward_boundary_id is not None
