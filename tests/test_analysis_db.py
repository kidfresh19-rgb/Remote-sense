"""End-to-end analysis-upsert tests against PostGIS (D3): additive + idempotent storage of an
engine result with its full provenance tuple. These need PostGIS, so they SKIP when no database
is reachable and run for real in CI / `docker compose`. Set RS_TEST_DATABASE_URL to point them at
a database; otherwise they try localhost."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Analysis, Farm, Field
from rs_core.repositories import upsert_analysis, upsert_scene_metadata
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_SCENE_ID = "S2A_MSIL2A_20241015T075"


def _square_mp(lon: float, lat: float, side: float) -> MultiPolygon:
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
async def sessionmaker_():
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


async def _seed_field_and_scene(maker) -> uuid.UUID:
    """A farm + field and a scene_metadata row, so the analysis FKs (field_id, scene_id) hold."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-AN-1",
            boundary=from_shape(_square_mp(31.05, -17.83, 0.02), srid=4326),
            centroid_lon=31.05,
            centroid_lat=-17.83,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(farm)
        await session.flush()
        field = Field(
            farm_id=farm.id,
            canonical_field_id="fld-1",
            boundary=from_shape(_square_mp(31.05, -17.83, 0.008), srid=4326),
            geometry_version=1,
            derived_from_farm=False,
            needs_backfill=True,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(field)
        await session.flush()
        field_id = field.id
        await upsert_scene_metadata(
            session,
            scene_id=_SCENE_ID,
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0, "B08": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2024, 10, 15, 7, 55, tzinfo=UTC),
        )
        await session.commit()
    return field_id


def _args(field_id: uuid.UUID, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "field_id": field_id,
        "scene_id": _SCENE_ID,
        "pass_date": date(2024, 10, 15),
        "index_name": "ndvi",
        "formula_version": "ndvi/v1",
        "geometry_version": 1,
        "provider": "cdse",
        "provider_scene_id": _SCENE_ID,
        "processing_mode": "windowed_cog",
        "resolution_m": 10.0,
        "clear_fraction": 0.9,
        "mean": 0.6,
        "min_val": 0.1,
        "max_val": 0.8,
        "std": 0.05,
        "p10": 0.3,
        "p90": 0.75,
        "confidence": "high",
    }
    base.update(over)
    return base


async def _count(maker) -> int:
    async with maker() as session:
        return (await session.execute(select(func.count()).select_from(Analysis))).scalar_one()


async def test_upsert_inserts_then_refreshes_idempotently(sessionmaker_) -> None:
    field_id = await _seed_field_and_scene(sessionmaker_)
    async with sessionmaker_() as session:
        row, created = await upsert_analysis(session, **_args(field_id))
        await session.commit()
    assert created is True
    assert row.mean == 0.6

    # Same scientific identity again: refreshed in place, not duplicated.
    async with sessionmaker_() as session:
        row2, created2 = await upsert_analysis(session, **_args(field_id, mean=0.61))
        await session.commit()
    assert created2 is False
    assert row2.mean == 0.61  # payload refreshed
    assert await _count(sessionmaker_) == 1


async def test_upsert_is_additive_across_identities(sessionmaker_) -> None:
    field_id = await _seed_field_and_scene(sessionmaker_)
    async with sessionmaker_() as session:
        await upsert_analysis(
            session, **_args(field_id, index_name="ndvi", formula_version="ndvi/v1")
        )
        await upsert_analysis(
            session,
            **_args(field_id, index_name="ndre", formula_version="ndre/v1", resolution_m=20.0),
        )
        await session.commit()
    assert await _count(sessionmaker_) == 2
