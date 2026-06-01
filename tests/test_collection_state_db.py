"""End-to-end collection-state tests against PostGIS (D5): get-or-create the per-field cursor,
advance the forward-fill watermark only forward, and flag backfill completion. These need
PostGIS, so they SKIP when no database is reachable and run for real in CI / `docker compose`.
Set RS_TEST_DATABASE_URL to point them at a database; otherwise they try localhost."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field, FieldCollectionState
from rs_core.repositories import (
    ensure_collection_state,
    mark_backfill_complete,
    record_forward_fill_poll,
)
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)


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
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-CS-1",
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
        await session.commit()
    return field_id


async def _count(maker) -> int:
    async with maker() as session:
        return (
            await session.execute(select(func.count()).select_from(FieldCollectionState))
        ).scalar_one()


async def test_ensure_is_idempotent(sessionmaker_) -> None:
    field_id = await _seed_field(sessionmaker_)
    async with sessionmaker_() as session:
        a = await ensure_collection_state(session, field_id=field_id, geometry_version=1)
        await session.commit()
        first_id = a.id
    async with sessionmaker_() as session:
        b = await ensure_collection_state(session, field_id=field_id, geometry_version=1)
        await session.commit()
        assert b.id == first_id
        assert b.backfill_complete is False
    assert await _count(sessionmaker_) == 1


async def test_forward_fill_poll_advances_only_forward(sessionmaker_) -> None:
    field_id = await _seed_field(sessionmaker_)
    t1 = datetime(2024, 11, 10, 8, 0, tzinfo=UTC)
    t2 = datetime(2024, 11, 15, 8, 0, tzinfo=UTC)
    async with sessionmaker_() as session:
        await record_forward_fill_poll(
            session,
            field_id=field_id,
            geometry_version=1,
            polled_at=t1,
            cursor_date=date(2024, 11, 8),
            last_scene_id="S2_NEW",
        )
        await session.commit()

    # A later poll that only saw an OLDER pass must not rewind the watermark.
    async with sessionmaker_() as session:
        state = await record_forward_fill_poll(
            session,
            field_id=field_id,
            geometry_version=1,
            polled_at=t2,
            cursor_date=date(2024, 11, 1),
            last_scene_id="S2_OLD",
        )
        await session.commit()
        assert state.last_poll_at == t2  # cadence stamp always updates
        assert state.cursor_date == date(2024, 11, 8)  # watermark held
        assert state.last_scene_id == "S2_NEW"  # bound to the watermark scene
    assert await _count(sessionmaker_) == 1


async def test_mark_backfill_complete(sessionmaker_) -> None:
    field_id = await _seed_field(sessionmaker_)
    done_at = datetime(2024, 11, 20, 9, 0, tzinfo=UTC)
    async with sessionmaker_() as session:
        state = await mark_backfill_complete(
            session,
            field_id=field_id,
            geometry_version=1,
            completed_at=done_at,
            cursor_date=date(2024, 11, 18),
        )
        await session.commit()
        assert state.backfill_complete is True
        assert state.backfill_completed_at == done_at
        assert state.last_poll_at == done_at  # backfill counts as the latest archive poll
        assert state.cursor_date == date(2024, 11, 18)
