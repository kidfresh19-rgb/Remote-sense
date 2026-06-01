"""End-to-end outbound-publish tests against PostGIS (L7, Phase 6): a farm's stored analyses are
built into an additive, geometry-free payload, pushed via a recording GatewayPort, and recorded in
the outbox; a re-publish is a DB-level no-op (R-2); a push failure dead-letters. These need
PostGIS, so they SKIP when no database is reachable and run for real in CI / `docker compose`."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field, SyncOutbox
from rs_core.repositories import upsert_analysis, upsert_scene_metadata
from rs_sync import RecordingGatewayPort
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.worker.publish import publish_farm

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_SCENE = "S2A_MSIL2A_20250115T075"
_NOW = datetime(2025, 1, 16, tzinfo=UTC)


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


async def _seed(maker) -> None:
    """A farm FARM-P1 + field fld-1 + scene metadata + NDVI/NDRE analyses for one pass."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-P1",
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
            scene_id=_SCENE,
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2025, 1, 15, 7, 55, tzinfo=UTC),
        )
        for index, res in (("ndvi", 10.0), ("ndre", 20.0)):
            await upsert_analysis(
                session,
                field_id=field_id,
                scene_id=_SCENE,
                pass_date=date(2025, 1, 15),
                index_name=index,
                formula_version="1",
                geometry_version=1,
                provider="cdse",
                provider_scene_id=_SCENE,
                processing_mode="windowed_cog",
                resolution_m=res,
                clear_fraction=0.9,
                mean=0.6,
                confidence="high",
            )
        await session.commit()


async def test_publish_pushes_geometry_free_payload_and_records(maker_) -> None:
    await _seed(maker_)
    gateway = RecordingGatewayPort()
    async with maker_() as session:
        summary = await publish_farm(session, gateway, canonical_farm_id="FARM-P1", now=_NOW)
        await session.commit()

    assert summary.status == "published"
    assert summary.results == 2
    assert len(gateway.pushed) == 1
    payload = gateway.pushed[0]
    assert payload.canonical_farm_id == "FARM-P1"
    assert {r.canonical_field_id for r in payload.results} == {"fld-1"}
    blob = payload.model_dump_json()
    assert "geometry" not in blob
    assert "coordinates" not in blob
    async with maker_() as session:
        outbox = (await session.execute(select(SyncOutbox))).scalar_one()
        assert outbox.status == "published"
        assert outbox.result_count == 2
        assert outbox.pushed_at is not None


async def test_publish_is_idempotent(maker_) -> None:
    await _seed(maker_)
    gateway = RecordingGatewayPort()
    async with maker_() as session:
        await publish_farm(session, gateway, canonical_farm_id="FARM-P1", now=_NOW)
        await session.commit()
    async with maker_() as session:
        again = await publish_farm(session, gateway, canonical_farm_id="FARM-P1", now=_NOW)
        await session.commit()

    assert again.status == "skipped"  # already published -> not re-pushed (R-2)
    assert len(gateway.pushed) == 1
    async with maker_() as session:
        n = (await session.execute(select(func.count()).select_from(SyncOutbox))).scalar_one()
    assert n == 1


async def test_publish_failure_dead_letters(maker_) -> None:
    await _seed(maker_)
    gateway = RecordingGatewayPort(ok=False)
    async with maker_() as session:
        summary = await publish_farm(session, gateway, canonical_farm_id="FARM-P1", now=_NOW)
        await session.commit()

    assert summary.status == "dead_letter"
    async with maker_() as session:
        outbox = (await session.execute(select(SyncOutbox))).scalar_one()
        assert outbox.status == "dead_letter"
        assert outbox.attempts == 1


async def test_pipeline_health_summary(maker_) -> None:
    from rs_core.repositories import pipeline_health

    await _seed(maker_)  # one field, needs_backfill=True
    async with maker_() as session:
        health = await pipeline_health(session)
    assert health["fields"] == 1
    assert health["awaiting_backfill"] == 1
    assert health["dead_letters"] == 0

    async with maker_() as session:  # a failed push -> a dead-letter shows up
        await publish_farm(
            session, RecordingGatewayPort(ok=False), canonical_farm_id="FARM-P1", now=_NOW
        )
        await session.commit()
    async with maker_() as session:
        assert (await pipeline_health(session))["dead_letters"] == 1
