"""Field-notes (annotation) persistence against PostGIS: a note is stored and read back newest
first, delete is scoped to its field and reports whether a row went, and a note cascades with its
field. These need PostGIS, so they SKIP when no database is reachable and run for real in CI /
`docker compose`."""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Annotation, Farm, Field
from rs_core.repositories import delete_annotation, insert_annotation, list_annotations
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


async def _seed_field(maker) -> uuid.UUID:
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-A1",
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
            geometry_version=2,
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
        return (await session.execute(select(func.count()).select_from(Annotation))).scalar_one()


async def test_insert_and_list_newest_first(maker_) -> None:
    field_id = await _seed_field(maker_)
    # Each note is its own request in production, so insert them in separate transactions:
    # func.now() is the transaction timestamp, so two inserts in one transaction tie on created_at;
    # this mirrors real usage and gives the ordering a real time gap to sort on.
    async with maker_() as session:
        first = await insert_annotation(
            session,
            field_id=field_id,
            geometry_version=2,
            pass_date=None,
            body="Whole-field observation.",
            author="analyst-1",
        )
        # created_at is server-assigned and pulled back on flush, so the row is fully populated.
        assert first.created_at is not None
        assert first.geometry_version == 2
        await session.commit()

    async with maker_() as session:
        await insert_annotation(
            session,
            field_id=field_id,
            geometry_version=2,
            pass_date=None,
            body="A later note.",
            author="analyst-2",
        )
        await session.commit()

    async with maker_() as session:
        notes = await list_annotations(session, field_id=field_id)
    assert [n.body for n in notes] == ["A later note.", "Whole-field observation."]
    assert notes[0].author == "analyst-2"


async def test_delete_is_scoped_to_field(maker_) -> None:
    field_id = await _seed_field(maker_)
    async with maker_() as session:
        note = await insert_annotation(
            session,
            field_id=field_id,
            geometry_version=2,
            pass_date=None,
            body="To be deleted.",
            author="analyst-1",
        )
        note_id = note.id
        await session.commit()

    async with maker_() as session:
        # A mismatched field id must not match the note, even with the right annotation id.
        wrong = await delete_annotation(
            session, annotation_id=note_id, field_id=uuid.uuid4()
        )
        assert wrong is False
        ok = await delete_annotation(session, annotation_id=note_id, field_id=field_id)
        assert ok is True
        # A second delete of the same id reports nothing removed (idempotent 404 path).
        again = await delete_annotation(session, annotation_id=note_id, field_id=field_id)
        assert again is False
        await session.commit()

    assert await _count(maker_) == 0


async def test_notes_cascade_with_field(maker_) -> None:
    field_id = await _seed_field(maker_)
    async with maker_() as session:
        await insert_annotation(
            session,
            field_id=field_id,
            geometry_version=2,
            pass_date=None,
            body="Pinned to a field that is about to be removed.",
            author="analyst-1",
        )
        await session.commit()

    async with maker_() as session:
        field = await session.get(Field, field_id)
        await session.delete(field)
        await session.commit()

    assert await _count(maker_) == 0
