"""DB-backed unit tests for the farm health analytics repository functions.

These tests run on a test PostGIS instance, seeding a mock farm with multiple fields

and checking calculations for area-weighted averages, status distributions, and anomalies.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field, FieldGeometryVersion
from rs_core.repositories import (
    get_farm_analytics_anomalies,
    get_farm_analytics_summary,
    get_farm_analytics_timeseries,
    upsert_analysis,
    upsert_scene_metadata,
)
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL",
    "postgresql+psycopg://rs:rs@localhost:5432/remote_sense",
)


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
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostGIS not reachable at {_TEST_DB_URL}: {exc}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_analytics_data(maker) -> None:
    """Seed FARM-1 with:

    - Field 1 (100 hectares, crop=maize): NDVI 0.8 (healthy)
    - Field 2 (50 hectares, crop=tobacco): NDVI 0.2 (critical / stressed)
    """
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-1",
            name="Alpha Farm",
            region="Mashonaland",
            boundary=from_shape(_mp(31.05, -17.83, 0.02), srid=4326),
            centroid_lon=31.05,
            centroid_lat=-17.83,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(farm)
        await session.flush()

        # Field 1: 100 hectares
        field1 = Field(
            farm_id=farm.id,
            canonical_field_id="1",
            name="Field 1",
            crop="maize",
            boundary=from_shape(_mp(31.05, -17.83, 0.01), srid=4326),
            geometry_version=1,
            derived_from_farm=False,
            needs_backfill=False,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(field1)

        # Field 2: 50 hectares
        field2 = Field(
            farm_id=farm.id,
            canonical_field_id="2",
            name="Field 2",
            crop="tobacco",
            boundary=from_shape(_mp(31.07, -17.83, 0.007), srid=4326),
            geometry_version=1,
            derived_from_farm=False,
            needs_backfill=False,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(field2)
        await session.flush()

        # Seed FieldGeometryVersion area
        fgv1 = FieldGeometryVersion(
            field_id=field1.id,
            version=1,
            boundary=field1.boundary,
            area_m2=1000000.0,  # 100 hectares
        )
        fgv2 = FieldGeometryVersion(
            field_id=field2.id,
            version=1,
            boundary=field2.boundary,
            area_m2=500000.0,  # 50 hectares
        )
        session.add_all([fgv1, fgv2])
        await session.flush()

        # Seed SceneMetadata
        await upsert_scene_metadata(
            session,
            scene_id="SCENE-1",
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2026, 5, 17, 7, 55, tzinfo=UTC),
        )

        # Analysis on pass 1: 2026-05-17
        await upsert_analysis(
            session,
            field_id=field1.id,
            scene_id="SCENE-1",
            pass_date=date(2026, 5, 17),
            index_name="ndvi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id="SCENE-1",
            processing_mode="windowed_cog",
            resolution_m=10.0,
            clear_fraction=0.95,
            mean=0.8,
        )
        await upsert_analysis(
            session,
            field_id=field2.id,
            scene_id="SCENE-1",
            pass_date=date(2026, 5, 17),
            index_name="ndvi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id="SCENE-1",
            processing_mode="windowed_cog",
            resolution_m=10.0,
            clear_fraction=0.95,
            mean=0.2,
        )

        # Seed previous pass to test anomaly drop
        await upsert_scene_metadata(
            session,
            scene_id="SCENE-0",
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2026, 5, 10, 7, 55, tzinfo=UTC),
        )
        # Field 2 had NDVI 0.5 on 2026-05-10 (so it dropped by 0.3, a sudden drop!)
        await upsert_analysis(
            session,
            field_id=field2.id,
            scene_id="SCENE-0",
            pass_date=date(2026, 5, 10),
            index_name="ndvi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id="SCENE-0",
            processing_mode="windowed_cog",
            resolution_m=10.0,
            clear_fraction=0.95,
            mean=0.5,
        )

        await session.commit()


async def test_get_farm_analytics_summary(maker_):
    await _seed_analytics_data(maker_)
    async with maker_() as session:
        summary = await get_farm_analytics_summary(session, "FARM-1")

    assert summary is not None
    assert summary["canonical_farm_id"] == "FARM-1"
    assert summary["farm_name"] == "Alpha Farm"
    assert summary["total_fields"] == 2
    assert summary["total_area_hectares"] == 150.0  # 100 + 50
    assert summary["crops"] == ["maize", "tobacco"]
    assert summary["latest_pass_date"] == date(2026, 5, 17)

    # Area-weighted average calculation:
    # (0.8 * 100 + 0.2 * 50) / 150 = (80 + 10) / 150 = 90 / 150 = 0.60
    assert summary["overall_health_score"] == 0.6
    assert summary["overall_health"] == "moderate"  # 0.6 falls into moderate/developing
    assert summary["field_status_counts"]["healthy"] == 1  # Field 1
    assert summary["field_status_counts"]["critical"] == 1  # Field 2


async def test_get_farm_analytics_timeseries(maker_):
    await _seed_analytics_data(maker_)
    async with maker_() as session:
        points = await get_farm_analytics_timeseries(session, "FARM-1", "ndvi")

    assert points is not None
    assert len(points) == 2  # 2026-05-10 and 2026-05-17

    # Point 1: 2026-05-10 (only Field 2 had NDVI 0.5)
    p1 = points[0]
    assert p1["pass_date"] == date(2026, 5, 10)
    assert p1["area_weighted_mean"] == 0.5
    assert p1["analyzed_fields"] == 1
    assert p1["health_distribution_pct"]["moderate"] == 100.0

    # Point 2: 2026-05-17 (Field 1: 0.8, Field 2: 0.2)
    p2 = points[1]
    assert p2["pass_date"] == date(2026, 5, 17)
    assert p2["area_weighted_mean"] == 0.6
    assert p2["analyzed_fields"] == 2
    # Area distribution: Field 1 (100ha / 150ha = 66.7%), Field 2 (50ha / 150ha = 33.3%)
    assert p2["health_distribution_pct"]["healthy"] == 66.7
    assert p2["health_distribution_pct"]["critical"] == 33.3


async def test_get_farm_analytics_anomalies(maker_):
    await _seed_analytics_data(maker_)
    async with maker_() as session:
        res = await get_farm_analytics_anomalies(session, "FARM-1", deviation_threshold=0.15)

    assert res is not None
    assert res["pass_date"] == date(2026, 5, 17)
    assert res["farm_average"] == 0.6

    anomalies = res["anomalies"]
    # Field 2 (tobacco, NDVI 0.2) is below farm average (0.6) by 0.4 (> 0.15) -> underperforming.
    # Field 2 dropped from 0.5 to 0.2 (drop 0.3 >= 0.2) -> sudden_drop.
    assert len(anomalies) == 1
    anom = anomalies[0]
    assert anom["canonical_field_id"] == "2"
    assert anom["crop"] == "tobacco"
    assert anom["field_value"] == 0.2
    assert "underperforming" in anom["anomaly_type"]
    assert "sudden_drop" in anom["anomaly_type"]
