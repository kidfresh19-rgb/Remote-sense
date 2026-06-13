"""End-to-end partition behavior against PostGIS (S4.1): `analysis` is a partitioned parent,
`create_all` environments get the DEFAULT catch-all, ensure_analysis_partitions creates exactly
the missing months idempotently, rows route to their month, and a month that cannot be created
(DEFAULT already holds its range) is skipped without failing the run. Needs PostGIS, so it SKIPS
when no database is reachable and runs for real in CI / `docker compose`. Set
RS_TEST_DATABASE_URL to point it at a database."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Analysis, Farm, Field, SceneMetadata
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.worker.partitions import ensure_analysis_partitions, partition_name

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)

_TODAY = date(2026, 6, 12)


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


async def _seed_field(maker) -> uuid.UUID:
    """One farm + field so the analysis field FK holds."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-PART-1",
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
        await session.commit()
        return field.id


async def _add_analysis(maker, field_id: uuid.UUID, scene_id: str, pass_date: date) -> None:
    """One scene_metadata row + one analysis on it, dated `pass_date`. The scene flushes first:
    no relationship() ties the two mappers, so the unit of work needs the explicit order for the
    scene_id FK to hold."""
    async with maker() as session:
        session.add(
            SceneMetadata(
                scene_id=scene_id,
                provider="mock",
                quantification_value=10000.0,
                boa_add_offset={"B04": -1000.0, "B08": -1000.0},
                crs="EPSG:32736",
                sensing_datetime=datetime(
                    pass_date.year, pass_date.month, pass_date.day, 8, 0, tzinfo=UTC
                ),
            )
        )
        await session.flush()
        session.add(
            Analysis(
                field_id=field_id,
                scene_id=scene_id,
                pass_date=pass_date,
                index_name="ndvi",
                mean=0.61,
                clear_fraction=0.9,
                resolution_m=10.0,
                formula_version="ndvi/v1",
                geometry_version=1,
                provider="mock",
                provider_scene_id=scene_id,
                processing_mode="mock",
            )
        )
        await session.commit()


async def _partition_of(maker, scene_id: str) -> str:
    async with maker() as session:
        return (
            await session.execute(
                text("SELECT tableoid::regclass::text FROM analysis WHERE scene_id = :sid"),
                {"sid": scene_id},
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_create_all_routes_inserts_to_default_partition(sessionmaker_) -> None:
    """A schema stood up by create_all (no migration, no maintenance run) accepts writes via the
    DEFAULT catch-all the model creates with the parent."""
    field_id = await _seed_field(sessionmaker_)
    await _add_analysis(sessionmaker_, field_id, "S2A_PART_DEFAULT", date(2026, 6, 1))
    assert await _partition_of(sessionmaker_, "S2A_PART_DEFAULT") == "analysis_default"


@pytest.mark.asyncio
async def test_ensure_creates_missing_months_then_reports_them_present(sessionmaker_) -> None:
    async with sessionmaker_() as session:
        first = await ensure_analysis_partitions(session, today=_TODAY, backfill_months=18)
        await session.commit()
    # 2024-11 (horizon floor minus slack) .. 2026-09 (three ahead), all new, nothing skipped.
    assert first.created[0] == "analysis_y2024m11"
    assert first.created[-1] == "analysis_y2026m09"
    assert len(first.created) == 23
    assert first.present == []
    assert first.skipped == []

    async with sessionmaker_() as session:
        second = await ensure_analysis_partitions(session, today=_TODAY, backfill_months=18)
        await session.commit()
    assert second.created == []
    assert second.skipped == []
    assert second.present == first.created


@pytest.mark.asyncio
async def test_rows_route_to_their_month_partition(sessionmaker_) -> None:
    field_id = await _seed_field(sessionmaker_)
    async with sessionmaker_() as session:
        await ensure_analysis_partitions(session, today=_TODAY, backfill_months=18)
        await session.commit()

    await _add_analysis(sessionmaker_, field_id, "S2A_PART_JUN", date(2026, 6, 10))
    await _add_analysis(sessionmaker_, field_id, "S2A_PART_JAN", date(2025, 1, 31))
    # Predates every monthly partition -> lands in the catch-all, still fully queryable.
    await _add_analysis(sessionmaker_, field_id, "S2A_PART_OLD", date(2020, 5, 15))

    assert await _partition_of(sessionmaker_, "S2A_PART_JUN") == partition_name(date(2026, 6, 1))
    assert await _partition_of(sessionmaker_, "S2A_PART_JAN") == partition_name(date(2025, 1, 1))
    assert await _partition_of(sessionmaker_, "S2A_PART_OLD") == "analysis_default"

    async with sessionmaker_() as session:
        total = (await session.execute(select(func.count()).select_from(Analysis))).scalar_one()
    assert total == 3


@pytest.mark.asyncio
async def test_occupied_default_month_is_skipped_not_fatal(sessionmaker_) -> None:
    """A horizon widened by config can plan months whose rows already sit in DEFAULT. Those
    months cannot become partitions (Postgres would have to move the rows), so the run reports
    them skipped, creates everything else, and the data stays correct where it is."""
    field_id = await _seed_field(sessionmaker_)
    await _add_analysis(sessionmaker_, field_id, "S2A_PART_OCC", date(2020, 5, 15))

    async with sessionmaker_() as session:
        summary = await ensure_analysis_partitions(
            session, today=date(2020, 6, 12), backfill_months=2
        )
        await session.commit()

    # Window 2020-03 .. 2020-09; only the occupied month fails, and only it.
    assert summary.skipped == [partition_name(date(2020, 5, 1))]
    assert partition_name(date(2020, 4, 1)) in summary.created
    assert partition_name(date(2020, 6, 1)) in summary.created
    assert len(summary.created) == 6

    # The row that blocked the month is untouched and still queryable through the parent.
    assert await _partition_of(sessionmaker_, "S2A_PART_OCC") == "analysis_default"
