"""Ward Watch enrollment persistence against PostGIS (backlog 0029): a household's full intercrop
mix round-trips with its derived dominant crop, the gateway identity join key and proxy
`geometry_source` are stored, deleting a household cascades to its plots and crop mix, and a crop is
unique per plot. These need PostGIS, so they SKIP when no database is reachable and run for real in
CI / `docker compose`."""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.cropmix import CropWeight, resolve_crop_mix
from rs_core.db import Base
from rs_core.models import CropMixEntry, Household, Plot
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
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


async def _seed_household_with_mix(maker) -> uuid.UUID:
    """A household with one officer-proxy plot intercropped maize-60 / cowpea-30 / squash-10."""
    resolved = resolve_crop_mix(
        [
            CropWeight("maize", 60),
            CropWeight("cowpea", 30),
            CropWeight("squash", 10),
        ]
    )
    async with maker() as session:
        household = Household(
            client_uuid=uuid.uuid4(),
            canonical_household_id="HH-WARD3-0001",
            ward_name="Ward 3",
            village="Chivhu",
            officer_id="AGRITEX-77",
        )
        session.add(household)
        await session.flush()
        plot = Plot(
            household_id=household.id,
            client_uuid=uuid.uuid4(),
            boundary=from_shape(_square_mp(31.05, -19.02, 0.002), srid=4326),
            geometry_source="officer_proxy",
            size_class="small_holding",
            planting_window="main",
            dominant_crop=resolved.dominant_crop,
        )
        session.add(plot)
        await session.flush()
        for entry in resolved.entries:
            session.add(CropMixEntry(plot_id=plot.id, crop=entry.crop, weight_pct=entry.weight_pct))
        await session.commit()
        return household.id


async def test_full_mix_and_dominant_round_trip(maker_) -> None:
    household_id = await _seed_household_with_mix(maker_)
    async with maker_() as session:
        plot = (
            await session.execute(select(Plot).where(Plot.household_id == household_id))
        ).scalar_one()
        assert plot.dominant_crop == "maize"
        assert plot.geometry_source == "officer_proxy"

        mix = (
            (await session.execute(select(CropMixEntry).where(CropMixEntry.plot_id == plot.id)))
            .scalars()
            .all()
        )
        assert {(e.crop, e.weight_pct) for e in mix} == {
            ("maize", 60.0),
            ("cowpea", 30.0),
            ("squash", 10.0),
        }

        household = (
            await session.execute(select(Household).where(Household.id == household_id))
        ).scalar_one()
        assert household.canonical_household_id == "HH-WARD3-0001"


async def test_deleting_household_cascades_to_plots_and_mix(maker_) -> None:
    household_id = await _seed_household_with_mix(maker_)
    async with maker_() as session:
        household = await session.get(Household, household_id)
        await session.delete(household)
        await session.commit()
    async with maker_() as session:
        plots = (await session.execute(select(func.count(Plot.id)))).scalar_one()
        entries = (await session.execute(select(func.count(CropMixEntry.id)))).scalar_one()
        assert plots == 0
        assert entries == 0


async def test_crop_is_unique_per_plot(maker_) -> None:
    household_id = await _seed_household_with_mix(maker_)
    async with maker_() as session:
        plot = (
            await session.execute(select(Plot).where(Plot.household_id == household_id))
        ).scalar_one()
        session.add(CropMixEntry(plot_id=plot.id, crop="maize", weight_pct=5.0))
        with pytest.raises(IntegrityError):
            await session.commit()
