"""DB-gated tests for the workspace BFF (L6): farms, fields (with geometry), per-field time series,
scenes, interpretations, and the annotation store (read + create + delete). These need PostGIS, so
they SKIP when no database is reachable and run for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from geoalchemy2.shape import from_shape
from pydantic import ValidationError
from rs_core import Principal, Role
from rs_core.db import Base
from rs_core.models import Farm, Field
from rs_core.repositories import insert_interpretation, upsert_analysis, upsert_scene_metadata
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.workspace import (
    AnnotationCreate,
    create_annotation_endpoint,
    delete_annotation_endpoint,
    field_audit,
    field_collect_endpoint,
    field_interpretations,
    field_scenes,
    field_timeseries,
    list_annotations_endpoint,
    list_farms,
    list_fields,
)

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_SCENE = "S2A_MSIL2A_20250115T075"
_PASS = date(2025, 1, 15)


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
    except Exception as exc:  # no DB reachable -> skip the whole DB-backed suite
        await engine.dispose()
        pytest.skip(f"PostGIS not reachable at {_TEST_DB_URL}: {exc}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(maker) -> uuid.UUID:
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-W1",
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
            canonical_field_id="fld-1",
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
        await upsert_analysis(
            session,
            field_id=field_id,
            scene_id=_SCENE,
            pass_date=_PASS,
            index_name="ndvi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id=_SCENE,
            processing_mode="windowed_cog",
            resolution_m=10.0,
            clear_fraction=0.9,
            mean=0.6,
            confidence="high",
        )
        await insert_interpretation(
            session,
            field_id=field_id,
            scene_id=_SCENE,
            pass_date=_PASS,
            geometry_version=1,
            prompt_version="interp/v1",
            crop="maize",
            narrative="Vigorous canopy.",
            status="vigorous",
            confidence="high",
            model="claude-opus-4-8",
        )
        await session.commit()
    return field_id


async def test_list_farms_and_fields(maker_) -> None:
    field_id = await _seed(maker_)
    async with maker_() as session:
        farms = await list_farms(session)
        assert [f.canonical_farm_id for f in farms] == ["FARM-W1"]
        fields = await list_fields(session, "FARM-W1")
    assert len(fields) == 1
    assert fields[0].field_id == str(field_id)
    assert fields[0].crop == "maize"
    assert fields[0].geometry["type"] in {"Polygon", "MultiPolygon"}  # geometry for the map


async def test_field_timeseries_and_scenes(maker_) -> None:
    field_id = await _seed(maker_)
    async with maker_() as session:
        series = await field_timeseries(session, field_id, "ndvi")
        scenes = await field_scenes(session, field_id)
    assert [p.pass_date for p in series] == [_PASS]
    assert series[0].mean == 0.6
    assert [s.scene_id for s in scenes] == [_SCENE]


async def test_field_interpretations(maker_) -> None:
    field_id = await _seed(maker_)
    async with maker_() as session:
        interps = await field_interpretations(session, field_id)
    assert len(interps) == 1
    assert interps[0].status == "vigorous"
    assert interps[0].published is False


async def test_field_audit(maker_) -> None:
    field_id = await _seed(maker_)
    # A second index on the same scene: two rows that share scene/geometry/formula, so the audit
    # query must return both with a deterministic order (not order-dependent assertions here).
    async with maker_() as session:
        await upsert_analysis(
            session,
            field_id=field_id,
            scene_id=_SCENE,
            pass_date=_PASS,
            index_name="ndmi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id=_SCENE,
            processing_mode="windowed_cog",
            resolution_m=20.0,
            clear_fraction=0.9,
            mean=0.2,
            confidence="high",
        )
        await session.commit()
    async with maker_() as session:
        records = await field_audit(session, field_id)
    assert len(records) == 2
    by_index = {r.index_name: r for r in records}
    assert set(by_index) == {"ndvi", "ndmi"}
    # The full provenance tuple travels with each analysis (CLAUDE.md invariant 5).
    record = by_index["ndvi"]
    assert record.scene_id == _SCENE
    assert record.provider == "cdse"
    assert record.provider_scene_id == _SCENE
    assert record.processing_mode == "windowed_cog"
    assert record.formula_version == "1"
    assert record.geometry_version == 1
    assert record.resolution_m == 10.0
    assert record.clear_fraction == 0.9


_ANALYST = Principal(subject="analyst-1", roles=frozenset({Role.ANALYST}))


async def test_annotation_create_list_delete(maker_) -> None:
    field_id = await _seed(maker_)
    async with maker_() as session:
        created = await create_annotation_endpoint(
            field_id,
            AnnotationCreate(body="  scout the NW corner  ", pass_date=_PASS),
            _ANALYST,
            session,
        )
        await session.commit()
    assert created.body == "scout the NW corner"  # trimmed by AnnotationCreate's StringConstraints
    assert created.geometry_version == 1  # read from the field, never trusted from the client
    assert created.author == "analyst-1"  # the verified token subject, not client input
    assert created.pass_date == _PASS

    async with maker_() as session:
        notes = await list_annotations_endpoint(field_id, _ANALYST, session)
    assert [n.body for n in notes] == ["scout the NW corner"]

    async with maker_() as session:
        await delete_annotation_endpoint(field_id, uuid.UUID(created.id), _ANALYST, session)
        await session.commit()
    async with maker_() as session:
        assert await list_annotations_endpoint(field_id, _ANALYST, session) == []


async def test_create_annotation_unknown_field_is_404(maker_) -> None:
    await _seed(maker_)
    async with maker_() as session:
        with pytest.raises(HTTPException) as excinfo:
            await create_annotation_endpoint(
                uuid.uuid4(), AnnotationCreate(body="orphan"), _ANALYST, session
            )
    assert excinfo.value.status_code == 404


async def test_field_collect_enqueues_backfill_for_existing_field(maker_, monkeypatch) -> None:
    import services.worker.tasks as tasks

    field_id = await _seed(maker_)
    enqueued: dict = {}
    monkeypatch.setattr(tasks.backfill_field, "delay", lambda fid: enqueued.update(fid=fid))
    async with maker_() as session:
        result = await field_collect_endpoint(field_id, _ANALYST, session)
    assert result["status"] == "enqueued"
    assert result["field_id"] == str(field_id)
    assert result["by"] == "analyst-1"  # the verified token subject, not client input
    assert enqueued["fid"] == str(field_id)  # the existing field's backfill was enqueued


async def test_field_collect_unknown_field_is_404(maker_, monkeypatch) -> None:
    import services.worker.tasks as tasks

    await _seed(maker_)
    # A 404 must short-circuit before any enqueue, so a missing field never schedules pipeline work.
    monkeypatch.setattr(
        tasks.backfill_field,
        "delay",
        lambda fid: pytest.fail("must not enqueue for a missing field"),
    )
    async with maker_() as session:
        with pytest.raises(HTTPException) as excinfo:
            await field_collect_endpoint(uuid.uuid4(), _ANALYST, session)
    assert excinfo.value.status_code == 404


def test_create_annotation_empty_body_is_422() -> None:
    # AnnotationCreate trims and requires a non-empty body, so an all-whitespace note is rejected at
    # request parse (Pydantic) and never reaches the endpoint.
    with pytest.raises(ValidationError):
        AnnotationCreate(body="   ")


async def test_field_scenes_deduplicates_by_pass_date(maker_) -> None:
    field_id = await _seed(maker_)
    # Insert another scene on the same date with lower clear_fraction, and another scene on a different date
    async with maker_() as session:
        # A duplicate date, lower clear_fraction (0.5 vs 0.9 in seeded)
        await upsert_scene_metadata(
            session,
            scene_id="S2B_MSIL2A_20250115T075_DUPE",
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2025, 1, 15, 8, 5, tzinfo=UTC),
        )
        await upsert_analysis(
            session,
            field_id=field_id,
            scene_id="S2B_MSIL2A_20250115T075_DUPE",
            pass_date=_PASS,
            index_name="ndvi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id="S2B_MSIL2A_20250115T075_DUPE",
            processing_mode="windowed_cog",
            resolution_m=10.0,
            clear_fraction=0.5, # lower than 0.9
            mean=0.5,
            confidence="medium",
        )
        # A different date (2025-01-20)
        await upsert_scene_metadata(
            session,
            scene_id="S2A_MSIL2A_20250120T075",
            provider="cdse",
            quantification_value=10000.0,
            boa_add_offset={"B04": -1000.0},
            crs="EPSG:32736",
            sensing_datetime=datetime(2025, 1, 20, 7, 55, tzinfo=UTC),
        )
        await upsert_analysis(
            session,
            field_id=field_id,
            scene_id="S2A_MSIL2A_20250120T075",
            pass_date=date(2025, 1, 20),
            index_name="ndvi",
            formula_version="1",
            geometry_version=1,
            provider="cdse",
            provider_scene_id="S2A_MSIL2A_20250120T075",
            processing_mode="windowed_cog",
            resolution_m=10.0,
            clear_fraction=0.8,
            mean=0.7,
            confidence="high",
        )
        await session.commit()

    async with maker_() as session:
        scenes = await field_scenes(session, field_id)
    # The duplicate date must return ONLY the one with highest clear_fraction (0.9, which has scene_id == _SCENE)
    assert [s.pass_date for s in scenes] == [_PASS, date(2025, 1, 20)]
    assert [s.scene_id for s in scenes] == [_SCENE, "S2A_MSIL2A_20250120T075"]
