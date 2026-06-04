"""Review/publish spine for interpretations against PostGIS (Phase A) plus the published-narrative
join used by the outbound contract (Phase C) and the cross-field queue (Phase E).

`review_interpretation` is the only path that can publish a read (risk #6): it stamps the reviewer,
clears `needs_review`, sets `published`, and may correct the narrative - but never the grounded
`status`/`confidence`. These need PostGIS, so they SKIP when no database is reachable and run for
real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field
from rs_core.repositories import (
    insert_interpretation,
    list_review_queue,
    published_narratives_for_farm,
    review_interpretation,
    upsert_analysis,
    upsert_scene_metadata,
)
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


async def _seed(maker) -> tuple[uuid.UUID, uuid.UUID]:
    """Farm "5" + maize field "4" + scene + NDVI/NDMI analyses + one drafted interpretation.
    Returns (field_id, interpretation_id). The farm id is an AgriTrack-style integer string so the
    same seed exercises the outbound narrative join."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="5",
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
            canonical_field_id="4",
            name="North block",
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
        row, _created = await insert_interpretation(
            session,
            field_id=field_id,
            scene_id=_SCENE_ID,
            pass_date=_PASS,
            geometry_version=1,
            prompt_version="interp/v1",
            crop="maize",
            narrative="Maize canopy is vigorous.",
            status="vigorous",
            confidence="high",
            model="claude-opus-4-8",
        )
        interp_id = row.id
        await session.commit()
    return field_id, interp_id


async def test_review_publishes_and_stamps_reviewer(maker_) -> None:
    field_id, interp_id = await _seed(maker_)
    async with maker_() as session:
        row = await review_interpretation(
            session,
            interpretation_id=interp_id,
            field_id=field_id,
            reviewer="agronomist@example.org",
            publish=True,
        )
        await session.commit()
    assert row is not None
    assert row.published is True
    assert row.needs_review is False
    assert row.reviewed_by == "agronomist@example.org"
    assert row.reviewed_at is not None
    # Grounded in the numbers, not the model: immutable on review.
    assert row.status == "vigorous"
    assert row.confidence == "high"
    # No narrative supplied -> the drafted text is kept.
    assert row.narrative == "Maize canopy is vigorous."


async def test_review_can_edit_then_unpublish(maker_) -> None:
    field_id, interp_id = await _seed(maker_)
    async with maker_() as session:
        await review_interpretation(
            session,
            interpretation_id=interp_id,
            field_id=field_id,
            reviewer="first",
            publish=True,
            narrative="Corrected: canopy vigorous, moisture low.",
        )
        await session.commit()
    async with maker_() as session:
        row = await review_interpretation(
            session,
            interpretation_id=interp_id,
            field_id=field_id,
            reviewer="second",
            publish=False,
        )
        await session.commit()
    assert row is not None
    assert row.narrative == "Corrected: canopy vigorous, moisture low."  # edit persisted
    assert row.published is False  # withdrawn
    assert row.needs_review is False  # still counts as reviewed
    assert row.reviewed_by == "second"


async def test_review_is_scoped_to_field(maker_) -> None:
    _field_id, interp_id = await _seed(maker_)
    other_field = uuid.uuid4()
    async with maker_() as session:
        row = await review_interpretation(
            session,
            interpretation_id=interp_id,
            field_id=other_field,
            reviewer="x",
            publish=True,
        )
    assert row is None  # a stale id from another field never matches -> 404 at the API


async def test_published_narratives_only_after_publish(maker_) -> None:
    field_id, interp_id = await _seed(maker_)
    async with maker_() as session:
        assert await published_narratives_for_farm(session, "5") == []  # nothing published yet
    async with maker_() as session:
        await review_interpretation(
            session,
            interpretation_id=interp_id,
            field_id=field_id,
            reviewer="a",
            publish=True,
            narrative="Published maize read.",
        )
        await session.commit()
    async with maker_() as session:
        narr = await published_narratives_for_farm(session, "5")
    assert narr == [("4", _PASS, "Published maize read.")]


async def test_review_queue_filters_and_carries_canonical_ids(maker_) -> None:
    field_id, interp_id = await _seed(maker_)
    async with maker_() as session:
        queue = await list_review_queue(session, needs_review=True)
    assert len(queue) == 1
    item = queue[0]
    assert item.canonical_farm_id == "5"
    assert item.canonical_field_id == "4"
    assert item.field_name == "North block"
    assert item.crop == "maize"
    assert item.needs_review is True
    assert item.published is False

    async with maker_() as session:
        await review_interpretation(
            session,
            interpretation_id=interp_id,
            field_id=field_id,
            reviewer="a",
            publish=True,
        )
        await session.commit()
    async with maker_() as session:
        assert await list_review_queue(session, needs_review=True) == []  # cleared from backlog
        assert len(await list_review_queue(session, needs_review=None)) == 1  # still in "all"
