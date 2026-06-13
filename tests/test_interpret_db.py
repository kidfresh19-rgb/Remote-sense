"""End-to-end interpretation-worker tests against PostGIS (Phase 4b, L4b): drafting a read for a
field/pass grounds the status in the stored NDVI band, stores it unpublished + needs_review, and
is idempotent (a second run reuses the draft without calling the model again). These need PostGIS,
so they SKIP when no database is reachable and run for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field, Interpretation
from rs_core.repositories import upsert_analysis, upsert_scene_metadata
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.worker.interpret import interpret_field_pass

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_SCENE_ID = "S2A_MSIL2A_20250115T075"
_PASS = date(2025, 1, 15)


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


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return "Maize canopy is vigorous; canopy moisture looks low."


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


async def _seed(maker) -> uuid.UUID:
    """A farm + maize field + scene metadata + NDVI/NDMI analyses for one pass."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-I1",
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
            crop="maize",
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
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2025, 1, 15, 7, 55, tzinfo=UTC),
        )
        for index, mean, res in (("ndvi", 0.72, 10.0), ("ndmi", 0.1, 20.0)):
            await upsert_analysis(
                session,
                field_id=field_id,
                scene_id=_SCENE_ID,
                pass_date=_PASS,
                index_name=index,
                formula_version="1",
                geometry_version=1,
                provider="cdse",
                provider_scene_id=_SCENE_ID,
                processing_mode="windowed_cog",
                resolution_m=res,
                clear_fraction=0.9,
                mean=mean,
                confidence="high",
            )
        await session.commit()
    return field_id


async def _count(maker) -> int:
    async with maker() as session:
        return (
            await session.execute(select(func.count()).select_from(Interpretation))
        ).scalar_one()


async def test_interpret_grounds_status_and_stores_unpublished(maker_) -> None:
    field_id = await _seed(maker_)
    client = _FakeClient()
    async with maker_() as session:
        result = await interpret_field_pass(
            session,
            client,
            field_id=field_id,
            scene_id=_SCENE_ID,
            geometry_version=1,
            crop="maize",
            model_id="claude-opus-4-8",
        )
        await session.commit()

    assert result is not None
    assert result.narrative.startswith("Maize canopy is vigorous")
    assert result.status == "vigorous"  # grounded from the NDVI band, not the model text
    assert result.confidence == "high"  # min clear fraction 0.9 -> high
    assert result.needs_review is True
    assert result.published is False
    assert result.prompt_version == "interp/v2"
    assert result.model == "claude-opus-4-8"
    assert client.calls == 1
    assert await _count(maker_) == 1


async def test_interpret_is_idempotent(maker_) -> None:
    field_id = await _seed(maker_)
    client = _FakeClient()
    async with maker_() as session:
        await interpret_field_pass(
            session,
            client,
            field_id=field_id,
            scene_id=_SCENE_ID,
            geometry_version=1,
            crop="maize",
            model_id="claude-opus-4-8",
        )
        await session.commit()
    async with maker_() as session:
        again = await interpret_field_pass(
            session,
            client,
            field_id=field_id,
            scene_id=_SCENE_ID,
            geometry_version=1,
            crop="maize",
            model_id="claude-opus-4-8",
        )
        await session.commit()

    assert again is not None
    assert client.calls == 1  # the second run reused the stored draft; no extra model call
    assert await _count(maker_) == 1
