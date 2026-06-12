"""End-to-end COG retention against PostGIS (S4.3): prune_cogs deletes stale-version and
aged-out COGs through the store, NULLs `cog_uri` on exactly those rows, and never touches the
stats/provenance columns. Needs PostGIS, so it SKIPS when no database is reachable and runs for
real in CI / `docker compose`. Set RS_TEST_DATABASE_URL to point it at a database."""

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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.worker.retention import prune_cogs

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)

_TODAY = date(2026, 6, 12)
_RECENT = date(2026, 6, 1)  # inside any sane horizon
_ANCIENT = date(2024, 1, 5)  # outside the 18-month default horizon


class FakeCogStore:
    """Records deletions; satisfies the CogStore protocol with no object store."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        raise AssertionError("retention never writes")

    def exists(self, key: str) -> bool:
        return key not in self.deleted

    def delete(self, key: str) -> None:
        self.deleted.append(key)


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


def _analysis(
    field_id: uuid.UUID,
    scene_id: str,
    pass_date: date,
    geometry_version: int,
    cog_uri: str | None,
) -> Analysis:
    return Analysis(
        field_id=field_id,
        scene_id=scene_id,
        pass_date=pass_date,
        index_name="ndvi",
        mean=0.62,
        clear_fraction=0.9,
        resolution_m=10.0,
        formula_version="ndvi/v1",
        geometry_version=geometry_version,
        provider="mock",
        provider_scene_id=scene_id,
        processing_mode="mock",
        cog_uri=cog_uri,
    )


async def _seed(maker) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """One farm, one field at geometry_version=2, three scenes, four analyses:
    stale-gv (recent pass, v1), aged-out (ancient pass, v2), kept (recent pass, v2), and an
    already-pruned ancient row (cog_uri NULL)."""
    async with maker() as session:
        farm = Farm(
            canonical_farm_id="FARM-RET-1",
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
            canonical_field_id="F-1",
            boundary=from_shape(_square_mp(31.05, -17.83, 0.01), srid=4326),
            geometry_version=2,
            source_crs="EPSG:4326",
            working_crs="EPSG:32736",
        )
        session.add(field)
        await session.flush()

        for scene_id, sensed in (
            ("S2_OLD", datetime(2024, 1, 5, 8, 30, tzinfo=UTC)),
            ("S2_NEW", datetime(2026, 6, 1, 8, 30, tzinfo=UTC)),
            ("S2_GONE", datetime(2024, 1, 5, 8, 30, tzinfo=UTC)),
        ):
            session.add(
                SceneMetadata(
                    scene_id=scene_id,
                    provider="mock",
                    quantification_value=10000.0,
                    boa_add_offset={"B04": -1000.0, "B08": -1000.0},
                    crs="EPSG:32736",
                    sensing_datetime=sensed,
                )
            )
        await session.flush()

        rows = {
            "stale": _analysis(
                field.id, "S2_NEW", _RECENT, 1, f"cog/v1/{field.id}/S2_NEW/ndvi.tif"
            ),
            "aged": _analysis(
                field.id, "S2_OLD", _ANCIENT, 2, f"cog/v2/{field.id}/S2_OLD/ndvi.tif"
            ),
            "kept": _analysis(field.id, "S2_NEW", _RECENT, 2, f"cog/v2/{field.id}/S2_NEW/ndvi.tif"),
            "gone": _analysis(field.id, "S2_GONE", _ANCIENT, 2, None),
        }
        session.add_all(rows.values())
        await session.commit()
        return field.id, {name: row.id for name, row in rows.items()}


async def test_prune_cogs_deletes_and_nulls_exactly_the_prunable(sessionmaker_) -> None:
    field_id, ids = await _seed(sessionmaker_)
    store = FakeCogStore()

    async with sessionmaker_() as session:
        summary = await prune_cogs(session, store, today=_TODAY, retention_months=18)
        await session.commit()

    assert summary.examined == 3  # the NULL-cog row is not examined
    assert summary.pruned_stale_version == 1
    assert summary.pruned_aged_out == 1
    assert sorted(store.deleted) == [
        f"cog/v1/{field_id}/S2_NEW/ndvi.tif",
        f"cog/v2/{field_id}/S2_OLD/ndvi.tif",
    ]

    async with sessionmaker_() as session:
        result = await session.execute(select(Analysis.id, Analysis.cog_uri, Analysis.mean))
        by_id = {row_id: (cog_uri, mean) for row_id, cog_uri, mean in result.all()}

    assert by_id[ids["stale"]][0] is None
    assert by_id[ids["aged"]][0] is None
    assert by_id[ids["kept"]][0] == f"cog/v2/{field_id}/S2_NEW/ndvi.tif"
    assert by_id[ids["gone"]][0] is None
    # Stats/provenance survive the prune (only the preview goes).
    assert all(mean == 0.62 for _, mean in by_id.values())


async def test_prune_cogs_is_idempotent(sessionmaker_) -> None:
    await _seed(sessionmaker_)
    store = FakeCogStore()

    async with sessionmaker_() as session:
        first = await prune_cogs(session, store, today=_TODAY, retention_months=18)
        await session.commit()
    async with sessionmaker_() as session:
        second = await prune_cogs(session, store, today=_TODAY, retention_months=18)
        await session.commit()

    assert first.pruned == 2
    assert second.examined == 1  # only the kept row still carries a COG
    assert second.pruned == 0
    assert len(store.deleted) == 2  # nothing re-deleted
