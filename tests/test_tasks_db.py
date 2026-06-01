"""End-to-end live-collection tests against PostGIS + the mock adapter (D4-live): a backfill run
persists scene metadata, per-index analyses and the cursor, clears needs_backfill, is idempotent
on re-run, and yields to a held lock. These need PostGIS, so they SKIP when no database is
reachable and run for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from rs_core import mark_backfill_complete
from rs_core.config import ImageryAdapter, Settings
from rs_core.db import Base
from rs_core.models import Analysis, Field, FieldCollectionState, SceneMetadata
from rs_core.schemas import FarmIn, FieldIn
from rs_imagery import get_access_adapter
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.ingestion import ingest_farm
from services.worker.planning import field_collection_key
from services.worker.tasks import (
    collect_pass,
    due_field_ids,
    field_to_aoi,
    plan_backfill_scenes,
    prepare_and_run,
)

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_HARARE = (31.05, -17.83)
_NOW = datetime(2025, 6, 1, 8, 0, tzinfo=UTC)


def _square(lon: float, lat: float, side: float) -> dict:
    h = side / 2
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - h, lat - h],
                [lon + h, lat - h],
                [lon + h, lat + h],
                [lon - h, lat + h],
                [lon - h, lat - h],
            ]
        ],
    }


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, name, value, *, nx=False, ex=None):
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def eval(self, script, numkeys, *args):
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


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


def _adapter():
    return get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))


async def _seed_field(maker) -> uuid.UUID:
    payload = FarmIn(
        canonical_farm_id="FARM-T1",
        boundary=_square(*_HARARE, 0.02),
        fields=[
            FieldIn(canonical_field_id="fld-1", crop="maize", geometry=_square(*_HARARE, 0.01))
        ],
    )
    async with maker() as session:
        await ingest_farm(session, payload)
        await session.commit()
    async with maker() as session:
        return (await session.execute(select(Field.id))).scalars().one()


async def _count(maker, model) -> int:
    async with maker() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_backfill_persists_and_marks_complete(maker_) -> None:
    field_id = await _seed_field(maker_)
    async with maker_() as session:
        summary = await prepare_and_run(
            session,
            _FakeRedis(),
            _adapter(),
            field_id=field_id,
            is_backfill=True,
            backfill_months=18,
            indices=["ndvi", "ndre"],
            now=_NOW,
        )
        await session.commit()

    assert summary.locked is False
    assert summary.scenes > 0
    assert summary.analyses == summary.scenes * 2  # two indices per scene
    assert await _count(maker_, SceneMetadata) >= 1
    assert await _count(maker_, Analysis) == summary.analyses
    async with maker_() as session:
        state = (await session.execute(select(FieldCollectionState))).scalar_one()
        assert state.backfill_complete is True
        assert state.cursor_date is not None
        field = (await session.execute(select(Field).where(Field.id == field_id))).scalar_one()
        assert field.needs_backfill is False


async def test_backfill_is_idempotent(maker_) -> None:
    field_id = await _seed_field(maker_)
    async with maker_() as session:
        await prepare_and_run(
            session,
            _FakeRedis(),
            _adapter(),
            field_id=field_id,
            is_backfill=True,
            backfill_months=18,
            indices=["ndvi"],
            now=_NOW,
        )
        await session.commit()
    first = await _count(maker_, Analysis)

    async with maker_() as session:
        again = await prepare_and_run(
            session,
            _FakeRedis(),
            _adapter(),
            field_id=field_id,
            is_backfill=True,
            backfill_months=18,
            indices=["ndvi"],
            now=_NOW,
        )
        await session.commit()
    assert again.scenes == 0  # every scene already processed -> nothing new
    assert await _count(maker_, Analysis) == first


async def test_held_lock_yields_without_writing(maker_) -> None:
    field_id = await _seed_field(maker_)
    redis = _FakeRedis()
    async with maker_() as session:
        field = (await session.execute(select(Field).where(Field.id == field_id))).scalar_one()
        redis.store[field_collection_key(str(field_id), field.geometry_version)] = "other-worker"
        summary = await prepare_and_run(
            session,
            redis,
            _adapter(),
            field_id=field_id,
            is_backfill=True,
            backfill_months=18,
            indices=["ndvi"],
            now=_NOW,
        )
        await session.commit()
    assert summary.locked is True
    assert await _count(maker_, Analysis) == 0


async def test_backfill_fan_out_plans_and_collects_one_pass(maker_) -> None:
    """D11: plan_backfill_scenes lists the window's outstanding passes; collect_pass collects a
    single one (only that scene lands, cursor advances, backfill stays incomplete); a re-plan then
    excludes the collected pass."""
    field_id = await _seed_field(maker_)
    adapter = _adapter()
    async with maker_() as session:
        field = (await session.execute(select(Field).where(Field.id == field_id))).scalar_one()
        gv = field.geometry_version
        aoi = field_to_aoi(field)
        plan = await plan_backfill_scenes(
            session, adapter, field_id=field_id, geometry_version=gv, aoi=aoi, months=18, now=_NOW
        )
    assert len(plan) > 1  # the window has several passes to fan out
    first_scene, first_date = plan[0]

    async with maker_() as session:
        summary = await collect_pass(
            session,
            _FakeRedis(),
            adapter,
            field_id=field_id,
            geometry_version=gv,
            aoi=aoi,
            scene_id=first_scene,
            pass_date=date.fromisoformat(first_date),
            indices=["ndvi", "ndre"],
            now=_NOW,
        )
        await session.commit()

    assert summary.locked is False
    assert summary.scenes == 1  # the one-day window resolves to exactly this pass
    assert summary.analyses == 2  # two indices
    async with maker_() as session:
        scene_ids = (await session.execute(select(Analysis.scene_id).distinct())).scalars().all()
        assert scene_ids == [first_scene]  # only the fanned-out pass collected
        state = (await session.execute(select(FieldCollectionState))).scalar_one()
        assert state.cursor_date is not None
        assert state.backfill_complete is False  # one pass does not complete the backfill

    async with maker_() as session:
        plan2 = await plan_backfill_scenes(
            session, adapter, field_id=field_id, geometry_version=gv, aoi=aoi, months=18, now=_NOW
        )
    assert first_scene not in {scene_id for scene_id, _ in plan2}
    assert len(plan2) == len(plan) - 1


async def test_due_field_ids_backfill_then_forward(maker_) -> None:
    field_id = await _seed_field(maker_)  # needs_backfill=True on creation

    async with maker_() as session:
        backfill, forward = await due_field_ids(session, now=_NOW)
        assert str(field_id) in backfill
        assert forward == []

    # Backfill done, last polled 6 days ago (> cadence) -> now due for forward-fill, not backfill.
    async with maker_() as session:
        await mark_backfill_complete(
            session, field_id=field_id, geometry_version=1, completed_at=_NOW - timedelta(days=6)
        )
        await session.execute(
            update(Field).where(Field.id == field_id).values(needs_backfill=False)
        )
        await session.commit()

    async with maker_() as session:
        backfill, forward = await due_field_ids(session, now=_NOW)
        assert backfill == []
        assert str(field_id) in forward
