"""End-to-end ingestion tests against a real PostGIS database: idempotent upsert, geometry
versioning (DI-5), and the no-field-farm derivation (DI-4).

These need PostGIS, so they SKIP when no database is reachable (e.g. a laptop with the
compose stack down) and run for real in CI / `docker compose`. Set RS_TEST_DATABASE_URL to
point them at a database; otherwise they try localhost."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.models import Farm, Field, FieldGeometryVersion, SceneMetadata
from rs_core.repositories import upsert_scene_metadata
from rs_core.schemas import FarmIn, FieldIn
from shapely.geometry import shape
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.ingestion import (
    IngestionError,
    _as_multipolygon,
    get_or_create_farm,
    get_or_create_field,
    ingest_farm,
)

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)


def _square(center_lon: float, center_lat: float, side_deg: float) -> dict:
    half = side_deg / 2
    lon, lat = center_lon, center_lat
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - half, lat - half],
                [lon + half, lat - half],
                [lon + half, lat + half],
                [lon - half, lat + half],
                [lon - half, lat - half],
            ]
        ],
    }


_HARARE = (31.05, -17.83)


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


async def _run(maker, payload: FarmIn):
    async with maker() as session:
        report = await ingest_farm(session, payload)
        await session.commit()
        return report


async def _count(maker, model) -> int:
    async with maker() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_ingest_creates_farm_and_field(sessionmaker_) -> None:
    payload = FarmIn(
        canonical_farm_id="FARM-001",
        name="Chinhoyi block A",
        boundary=_square(*_HARARE, 0.02),
        fields=[
            FieldIn(canonical_field_id="fld-1", crop="maize", geometry=_square(*_HARARE, 0.008))
        ],
    )
    report = await _run(sessionmaker_, payload)
    assert report.action == "created"
    assert report.working_crs == "EPSG:32736"  # Harare is east of the split
    assert len(report.fields) == 1
    assert report.fields[0].action == "created"
    assert report.fields[0].geometry_version == 1
    assert await _count(sessionmaker_, FieldGeometryVersion) == 1


async def test_ingest_is_idempotent(sessionmaker_) -> None:
    payload = FarmIn(
        canonical_farm_id="FARM-002",
        boundary=_square(*_HARARE, 0.02),
        fields=[FieldIn(canonical_field_id="fld-1", geometry=_square(*_HARARE, 0.008))],
    )
    await _run(sessionmaker_, payload)
    second = await _run(sessionmaker_, payload)

    assert second.action == "unchanged"
    assert second.fields[0].action == "unchanged"
    assert second.fields[0].geometry_version == 1
    # No duplicate rows, no extra geometry versions.
    assert await _count(sessionmaker_, Field) == 1
    assert await _count(sessionmaker_, FieldGeometryVersion) == 1


async def test_geometry_change_bumps_version(sessionmaker_) -> None:
    base = FarmIn(
        canonical_farm_id="FARM-003",
        boundary=_square(*_HARARE, 0.03),
        fields=[FieldIn(canonical_field_id="fld-1", geometry=_square(*_HARARE, 0.008))],
    )
    await _run(sessionmaker_, base)

    changed = FarmIn(
        canonical_farm_id="FARM-003",
        boundary=_square(*_HARARE, 0.03),
        fields=[FieldIn(canonical_field_id="fld-1", geometry=_square(*_HARARE, 0.012))],
    )
    report = await _run(sessionmaker_, changed)

    assert report.fields[0].action == "updated_geometry"
    assert report.fields[0].geometry_version == 2
    assert await _count(sessionmaker_, FieldGeometryVersion) == 2

    async with sessionmaker_() as session:
        field = (await session.execute(select(Field))).scalar_one()
        assert field.geometry_version == 2
        assert field.needs_backfill is True
        versions = (
            (
                await session.execute(
                    select(FieldGeometryVersion).order_by(FieldGeometryVersion.version)
                )
            )
            .scalars()
            .all()
        )
        assert versions[0].valid_to is not None  # prior boundary archived (retained)
        assert versions[1].valid_to is None  # current boundary is open


async def test_no_field_farm_derives_field(sessionmaker_) -> None:
    payload = FarmIn(canonical_farm_id="FARM-004", boundary=_square(*_HARARE, 0.02), fields=[])
    report = await _run(sessionmaker_, payload)

    assert len(report.fields) == 1
    assert report.fields[0].action == "derived_from_farm"
    async with sessionmaker_() as session:
        field = (await session.execute(select(Field))).scalar_one()
        assert field.derived_from_farm is True


async def test_non_nested_field_rejected(sessionmaker_) -> None:
    payload = FarmIn(
        canonical_farm_id="FARM-005",
        boundary=_square(*_HARARE, 0.02),
        fields=[FieldIn(canonical_field_id="rogue", geometry=_square(31.5, -17.83, 0.01))],
    )
    with pytest.raises(IngestionError, match="not nested"):
        await _run(sessionmaker_, payload)


async def test_scene_metadata_upsert_creates(sessionmaker_) -> None:
    async with sessionmaker_() as session:
        row, created = await upsert_scene_metadata(
            session,
            scene_id="S2A_MSIL2A_20240115T075",
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0, "B08": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2024, 1, 15, 7, 55, tzinfo=UTC),
            processing_baseline="04.00",
        )
        await session.commit()
    assert created is True
    assert row.quantification_value == 10000.0
    assert await _count(sessionmaker_, SceneMetadata) == 1


async def test_scene_metadata_upsert_is_immutable_and_idempotent(sessionmaker_) -> None:
    args = dict(
        scene_id="S2A_MSIL2A_20240115T075",
        provider="cdse",
        quantification_value=10000.0,
        boa_add_offset={"B04": -1000.0},
        crs="EPSG:32736",
        sensing_datetime=datetime(2024, 1, 15, 7, 55, tzinfo=UTC),
    )
    async with sessionmaker_() as session:
        await upsert_scene_metadata(session, **args)
        await session.commit()

    # A second call with a DIVERGING value must not overwrite the stored constant.
    async with sessionmaker_() as session:
        row, created = await upsert_scene_metadata(session, **{**args, "quantification_value": 1.0})
        await session.commit()

    assert created is False
    assert row.quantification_value == 10000.0  # first write wins
    assert await _count(sessionmaker_, SceneMetadata) == 1


async def test_get_or_create_farm_created_then_existing(sessionmaker_) -> None:
    """D8: against real PostgreSQL, the savepoint insert returns created=True, and a second
    call resolves the existing row (created=False) without duplicating it."""

    def _build() -> Farm:
        return Farm(
            canonical_farm_id="GC-1",
            boundary=from_shape(_as_multipolygon(shape(_square(*_HARARE, 0.02))), srid=4326),
            centroid_lon=_HARARE[0],
            centroid_lat=_HARARE[1],
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )

    async with sessionmaker_() as session:
        _, created1 = await get_or_create_farm(session, canonical_farm_id="GC-1", build=_build)
        await session.commit()
    async with sessionmaker_() as session:
        _, created2 = await get_or_create_farm(session, canonical_farm_id="GC-1", build=_build)
        await session.commit()

    assert created1 is True
    assert created2 is False
    assert await _count(sessionmaker_, Farm) == 1


async def test_get_or_create_field_created_then_existing(sessionmaker_) -> None:
    """D10: against real PostgreSQL the keyed-field savepoint insert returns created=True, and a
    second call for the same (farm, canonical_field_id) recovers the existing row (created=False)
    through the unique-violation path, without duplicating it."""

    def _build_farm() -> Farm:
        return Farm(
            canonical_farm_id="GC-F",
            boundary=from_shape(_as_multipolygon(shape(_square(*_HARARE, 0.02))), srid=4326),
            centroid_lon=_HARARE[0],
            centroid_lat=_HARARE[1],
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )

    async with sessionmaker_() as session:
        farm, _ = await get_or_create_farm(session, canonical_farm_id="GC-F", build=_build_farm)
        await session.flush()

        def _build_field() -> Field:
            return Field(
                farm_id=farm.id,
                canonical_field_id="f-1",
                boundary=from_shape(_as_multipolygon(shape(_square(*_HARARE, 0.008))), srid=4326),
                geometry_version=1,
                derived_from_farm=False,
                needs_backfill=True,
                source_crs="EPSG:4326",
                working_crs="EPSG:32736",
            )

        f1, created1 = await get_or_create_field(
            session, build=_build_field, farm_id=farm.id, canonical_field_id="f-1"
        )
        await session.flush()
        f2, created2 = await get_or_create_field(
            session, build=_build_field, farm_id=farm.id, canonical_field_id="f-1"
        )
        await session.commit()

    assert created1 is True
    assert created2 is False
    assert f2.id == f1.id
    assert await _count(sessionmaker_, Field) == 1
