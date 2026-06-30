"""Ward Watch live cohort assembly against PostGIS (backlog 0032).

Seeds households with a Natural Region + ward, cohort-keyed plots, and stored per-plot NDVI series,
then drives `assess_household_cohorts` (the seam the triage / rollup endpoints call). It asserts the
queue is non-empty over real rows, the movement lens flags the one declining household in an
otherwise-stable cohort as idiosyncratic, the ward filter scopes the result, and a household with no
Natural Region assignment is left out. Needs PostGIS, so it SKIPs when no database is reachable and
runs for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.cohorts import CohortLevel
from rs_core.db import Base
from rs_core.models import Household, Plot, PlotAnalysis
from rs_core.movement import MovementLabel
from rs_core.repositories import assess_household_cohorts
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)

# Half-window medians: STABLE moves 0.0, DECLINING moves -0.2 (past the 0.1 test threshold).
STABLE = [0.6, 0.6, 0.6, 0.6]
DECLINING = [0.6, 0.6, 0.4, 0.4]


def _square_mp(lon: float, lat: float, side: float) -> MultiPolygon:
    h = side / 2
    return MultiPolygon(
        [Polygon([(lon - h, lat - h), (lon + h, lat - h), (lon + h, lat + h), (lon - h, lat + h)])]
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


async def _seed_household(
    session,
    *,
    canonical: str,
    series: list[float],
    ward: str = "Ward 7",
    nr: str = "Region III",
    crop: str = "maize",
    window: str = "main",
    low_pixels: bool = False,
    lon: float = 30.0,
) -> None:
    """A household with one cohort-keyed plot carrying a stored NDVI series."""
    household = Household(
        client_uuid=uuid.uuid4(),
        canonical_household_id=canonical,
        ward_name=ward,
        dominant_nr=nr,
        village="Chivhu",
    )
    session.add(household)
    await session.flush()
    plot = Plot(
        client_uuid=uuid.uuid4(),
        household_id=household.id,
        boundary=from_shape(_square_mp(lon, -17.0, 0.002), srid=4326),
        geometry_source="officer_proxy",
        size_class="small_holding",
        dominant_crop=crop,
        planting_window=window,
    )
    session.add(plot)
    await session.flush()
    start = date(2025, 1, 1)
    for i, value in enumerate(series):
        session.add(
            PlotAnalysis(
                plot_id=plot.id,
                scene_id=f"S2_{canonical}_{i}",
                pass_date=start + timedelta(days=10 * i),
                index_name="ndvi",
                mean=value,
                clear_fraction=0.9,
                resolution_m=10.0,
                pixels=2 if low_pixels else 10,
                low_pixel_quality=low_pixels,
                formula_version="ndvi-v1",
                provider="mock",
                provider_scene_id=f"prov_{canonical}_{i}",
                processing_mode="mock",
            )
        )


async def test_assess_flags_the_one_declining_household_as_idiosyncratic(maker_) -> None:
    async with maker_() as session:
        await _seed_household(session, canonical="HH-STABLE-1", series=STABLE)
        await _seed_household(session, canonical="HH-STABLE-2", series=STABLE)
        await _seed_household(session, canonical="HH-STABLE-3", series=STABLE)
        await _seed_household(session, canonical="HH-FAIL", series=DECLINING, low_pixels=True)
        await session.commit()

    async with maker_() as session:
        result = await assess_household_cohorts(
            session, n_min=1, decline_threshold=0.1, clear_floor=0.5
        )

    by_id = {a.household_id: a for a in result}
    assert set(by_id) == {"HH-STABLE-1", "HH-STABLE-2", "HH-STABLE-3", "HH-FAIL"}
    failing = by_id["HH-FAIL"]
    assert failing.label == MovementLabel.IDIOSYNCRATIC  # declining while its cohort holds
    assert failing.cohort_level == CohortLevel.WARD_CROP_WINDOW
    assert failing.cohort_meets_quorum is True
    assert failing.low_pixel_quality is True  # §4 flag carried through
    assert failing.ward == "Ward 7"
    assert failing.dominant_nr == "Region III"
    assert by_id["HH-STABLE-1"].label == MovementLabel.NOMINAL


async def test_ward_filter_scopes_the_result(maker_) -> None:
    async with maker_() as session:
        await _seed_household(session, canonical="HH-W7", series=STABLE, ward="Ward 7")
        await _seed_household(session, canonical="HH-W8", series=STABLE, ward="Ward 8")
        await session.commit()

    async with maker_() as session:
        only_w7 = await assess_household_cohorts(
            session, ward="ward 7", n_min=1, decline_threshold=0.1, clear_floor=0.5
        )
    assert [a.household_id for a in only_w7] == ["HH-W7"]


async def test_household_without_natural_region_is_excluded(maker_) -> None:
    async with maker_() as session:
        await _seed_household(session, canonical="HH-OK", series=STABLE)
        # An enrolled household whose centroid placement (0031) has not run yet: no NR, no cohort.
        unplaced = Household(
            client_uuid=uuid.uuid4(),
            canonical_household_id="HH-NO-NR",
            ward_name="Ward 7",
            dominant_nr=None,
            village="Chivhu",
        )
        session.add(unplaced)
        await session.flush()
        session.add(
            Plot(
                client_uuid=uuid.uuid4(),
                household_id=unplaced.id,
                boundary=from_shape(_square_mp(30.0, -17.0, 0.002), srid=4326),
                geometry_source="officer_proxy",
                dominant_crop="maize",
                planting_window="main",
            )
        )
        await session.commit()

    async with maker_() as session:
        result = await assess_household_cohorts(
            session, n_min=1, decline_threshold=0.1, clear_floor=0.5
        )
    assert [a.household_id for a in result] == ["HH-OK"]


async def test_empty_database_yields_empty_queue(maker_) -> None:
    async with maker_() as session:
        result = await assess_household_cohorts(session, n_min=1, decline_threshold=0.1)
    assert result == []
