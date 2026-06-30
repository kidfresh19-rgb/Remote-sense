"""Ward Watch physical-visit package against PostGIS (backlog 0037, PRD 0003 §7.3).

Seeds households with plots + stored NDVI series and drives `get_household_visit_package` (the seam
the visit endpoint calls). It asserts the package assembles the per-plot trend and the orthophoto
reference (geometry + latest scene/date), the cohort movement assessment, and index-grounded alert
hints framed as hints; that a systemic decline raises a possible-failure hint with officer
questions; and that an unknown household is None (the endpoint turns it into a 404). Needs PostGIS,
so it SKIPs when no database is reachable and runs for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.alert_hints import HINT_FRAMING, AlertCategory
from rs_core.db import Base
from rs_core.models import Household, Plot, PlotAnalysis
from rs_core.movement import MovementLabel
from rs_core.repositories import get_household_visit_package
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)

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


async def _seed_household(session, *, canonical: str, series: list[float] | None) -> None:
    household = Household(
        client_uuid=uuid.uuid4(),
        canonical_household_id=canonical,
        ward_name="Ward 7",
        dominant_nr="Region III",
        village="Chivhu",
    )
    session.add(household)
    await session.flush()
    plot = Plot(
        client_uuid=uuid.uuid4(),
        household_id=household.id,
        boundary=from_shape(_square_mp(30.0, -17.0, 0.002), srid=4326),
        geometry_source="officer_proxy",
        size_class="small_holding",
        dominant_crop="maize",
        planting_window="main",
    )
    session.add(plot)
    await session.flush()
    start = date(2025, 1, 1)
    for i, value in enumerate(series or []):
        session.add(
            PlotAnalysis(
                plot_id=plot.id,
                scene_id=f"S2_{canonical}_{i}",
                pass_date=start + timedelta(days=10 * i),
                index_name="ndvi",
                mean=value,
                clear_fraction=0.9,
                resolution_m=10.0,
                pixels=10,
                low_pixel_quality=False,
                formula_version="ndvi-v1",
                provider="mock",
                provider_scene_id=f"prov_{canonical}_{i}",
                processing_mode="mock",
            )
        )


async def test_visit_package_assembles_trend_orthophoto_ref_and_assessment(maker_) -> None:
    # An idiosyncratic household: it declines while three peers hold.
    async with maker_() as session:
        await _seed_household(session, canonical="HH-A", series=STABLE)
        await _seed_household(session, canonical="HH-B", series=STABLE)
        await _seed_household(session, canonical="HH-C", series=STABLE)
        await _seed_household(session, canonical="HH-FAIL", series=DECLINING)
        await session.commit()

    async with maker_() as session:
        package = await get_household_visit_package(
            session, "HH-FAIL", n_min=1, decline_threshold=0.1, clear_floor=0.5
        )

    assert package is not None
    assert package.household_id == "HH-FAIL"
    assert package.ward == "Ward 7"
    assert package.dominant_nr == "Region III"
    assert package.dominant_crop == "maize"
    assert len(package.plots) == 1
    plot = package.plots[0]
    assert len(plot.trend) == len(DECLINING)
    assert plot.geometry["type"] == "MultiPolygon"  # geometry for the map + orthophoto request
    assert plot.latest_scene_id == "S2_HH-FAIL_3"  # latest pass = orthophoto reference
    assert plot.latest_pass_date is not None
    assert package.assessment is not None
    assert package.assessment.label == MovementLabel.IDIOSYNCRATIC


async def test_systemic_decline_raises_a_possible_failure_hint_framed_as_a_hint(maker_) -> None:
    async with maker_() as session:
        await _seed_household(session, canonical="HH-1", series=DECLINING)
        await _seed_household(session, canonical="HH-2", series=DECLINING)
        await _seed_household(session, canonical="HH-3", series=DECLINING)
        await session.commit()

    async with maker_() as session:
        package = await get_household_visit_package(
            session, "HH-1", n_min=1, decline_threshold=0.1, clear_floor=0.5
        )

    assert package is not None
    assert package.assessment is not None
    assert package.assessment.label == MovementLabel.SYSTEMIC
    categories = {h.category for h in package.alert_hints}
    assert AlertCategory.POSSIBLE_FAILURE in categories
    assert all(h.framing == HINT_FRAMING for h in package.alert_hints)  # hints, not diagnoses
    assert package.recommended_questions  # the officer's prompts for the hint


async def test_household_without_series_assembles_an_empty_but_valid_package(maker_) -> None:
    async with maker_() as session:
        await _seed_household(session, canonical="HH-EMPTY", series=None)
        await session.commit()

    async with maker_() as session:
        package = await get_household_visit_package(
            session, "HH-EMPTY", n_min=1, decline_threshold=0.1, clear_floor=0.5
        )

    assert package is not None
    assert package.plots[0].trend == []
    assert package.assessment is None  # no series to classify
    assert package.alert_hints == []
    assert package.previous_visits == []  # seam: 0038
    assert package.drone_reference is None  # seam: gateway drone ref


async def test_unknown_household_is_none(maker_) -> None:
    async with maker_() as session:
        package = await get_household_visit_package(session, "NOPE", n_min=1, decline_threshold=0.1)
    assert package is None
