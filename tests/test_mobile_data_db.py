"""DB-gated test for the AgriTrack pull endpoint (GET /api/v1/mobile/data, ADR 0006 P4): a farm's
stored analyses come back in the contract shape. Needs PostGIS, so it SKIPS when no database is
reachable and runs for real in CI / docker compose."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field
from rs_core.repositories import upsert_analysis, upsert_scene_metadata
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.integrations import farm_satellite_results

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_SCENE = "S2A_MSIL2A_20260517T075"
_PASS = date(2026, 5, 17)


def _mp(lon: float, lat: float, side: float) -> MultiPolygon:
    h = side / 2
    return MultiPolygon(
        [
            Polygon(
                [
                    (lon - h, lat - h),
                    (lon + h, lat - h),
                    (lon + h, lat + h),
                    (lon - h, lat + h),
                    (lon - h, lat - h),
                ]
            )
        ]
    )


@pytest_asyncio.fixture
async def maker_():
    engine = create_async_engine(_TEST_DB_URL, connect_args={"connect_timeout": 2})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # no DB reachable -> skip
        await engine.dispose()
        pytest.skip(f"PostGIS not reachable at {_TEST_DB_URL}: {exc}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_agritrack_farm(maker) -> None:
    """An AgriTrack-style farm (integer canonical id "2", field "4") with three index analyses on
    one pass."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="2",
            boundary=from_shape(_mp(31.05, -17.83, 0.02), srid=4326),
            centroid_lon=31.05,
            centroid_lat=-17.83,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(farm)
        await session.flush()
        field = Field(
            farm_id=farm.id,
            canonical_field_id="4",
            crop="maize",
            boundary=from_shape(_mp(31.05, -17.83, 0.008), srid=4326),
            geometry_version=1,
            derived_from_farm=False,
            needs_backfill=True,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(field)
        await session.flush()
        await upsert_scene_metadata(
            session,
            scene_id=_SCENE,
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2026, 5, 17, 7, 55, tzinfo=UTC),
        )
        for index_name, mean in (("ndvi", 0.62), ("evi2", 0.55), ("ndmi", 0.40)):
            await upsert_analysis(
                session,
                field_id=field.id,
                scene_id=_SCENE,
                pass_date=_PASS,
                index_name=index_name,
                formula_version="1",
                geometry_version=1,
                provider="cdse",
                provider_scene_id=_SCENE,
                processing_mode="windowed_cog",
                resolution_m=10.0,
                clear_fraction=0.95,
                mean=mean,
                confidence="high",
            )
        await session.commit()


async def test_mobile_data_pull_returns_contract_records(maker_):
    await _seed_agritrack_farm(maker_)
    async with maker_() as session:
        records = await farm_satellite_results(session, "2")
    assert len(records) == 2  # farm record + field record
    rec_farm = records[0]
    assert rec_farm.farmId == 2
    assert rec_farm.fieldId is None
    assert rec_farm.subPlotId is None
    assert rec_farm.scope == "farm"
    assert rec_farm.analysisDate == "2026-05-17"
    assert rec_farm.metrics.ndvi_mean == 0.62

    rec_field = records[1]
    assert rec_field.farmId == 2
    assert rec_field.fieldId == 4
    assert rec_field.subPlotId is None
    assert rec_field.scope == "field"
    assert rec_field.analysisDate == "2026-05-17"
    assert rec_field.metrics.ndvi_mean == 0.62
    assert rec_field.metrics.evi_mean == 0.55
    assert rec_field.metrics.ndwi_mean == 0.40  # NDMI -> ndwi_mean
    assert rec_field.metrics.classification == "healthy"
    assert rec_field.extId == "sat-2-4-2026-05-17"


async def test_mobile_data_pull_empty_for_unknown_farm(maker_):
    await _seed_agritrack_farm(maker_)
    async with maker_() as session:
        records = await farm_satellite_results(session, "999")
    assert records == []
