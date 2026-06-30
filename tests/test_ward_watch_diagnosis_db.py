"""Ward Watch field-diagnosis capture against PostGIS (backlog 0038).

Exercises the flywheel write + read end to end: the repo persists a controlled-vocab diagnosis
linked to its plot + provenance scene and reads it back as a labelled set; the record endpoint
stamps the verified officer, 422s an unknown vocab value, 404s an unknown plot; and a recorded
diagnosis surfaces in the household's visit-package `previous_visits` (closing the 0037 seam). Needs
PostGIS, so it SKIPs when no DB is reachable and runs for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from geoalchemy2.shape import from_shape
from rs_core.db import Base
from rs_core.diagnosis import validate_diagnosis
from rs_core.models import Diagnosis, Household, Plot
from rs_core.rbac import Principal, Role
from rs_core.repositories import (
    diagnoses_for_household,
    get_household_visit_package,
    list_diagnoses,
    record_diagnosis,
)
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.api.workspace.ward_watch import (
    DiagnosisIn,
    ward_watch_diagnoses_endpoint,
    ward_watch_record_diagnosis_endpoint,
)

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_OFFICER = Principal(subject="AGRITEX-1", roles=frozenset({Role.WARD_OFFICER}))


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


async def _seed_household_with_plot(maker) -> tuple[uuid.UUID, uuid.UUID]:
    async with maker() as session:
        household = Household(
            client_uuid=uuid.uuid4(),
            canonical_household_id="HH-DX-1",
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
            dominant_crop="maize",
            planting_window="main",
        )
        session.add(plot)
        await session.flush()
        ids = (household.id, plot.id)
        await session.commit()
        return ids


async def test_record_and_read_diagnosis_via_repo(maker_) -> None:
    household_id, plot_id = await _seed_household_with_plot(maker_)
    fields = validate_diagnosis(
        observed_crop="maize",
        condition="water_stress",
        cause="insufficient_rain",
        recommended_action="irrigate",
        notes="visible wilting",
    )
    async with maker_() as session:
        await record_diagnosis(
            session,
            plot_id=plot_id,
            fields=fields,
            observed_on=date(2025, 2, 10),
            scene_id="S2_OBS_1",
            officer_id="AGRITEX-1",
        )
        await session.commit()

    async with maker_() as session:
        stored = (
            (await session.execute(select(Diagnosis).where(Diagnosis.plot_id == plot_id)))
            .scalars()
            .all()
        )
        assert len(stored) == 1
        row = stored[0]
        assert (row.observed_crop, row.condition, row.cause) == (
            "maize",
            "water_stress",
            "insufficient_rain",
        )
        assert row.recommended_action == "irrigate"
        assert row.scene_id == "S2_OBS_1"
        assert row.officer_id == "AGRITEX-1"

        labelled = await list_diagnoses(session, ward="ward 7")
        assert [d.id for d in labelled] == [row.id]
        for_household = await diagnoses_for_household(session, household_id)
        assert [d.id for d in for_household] == [row.id]


async def test_record_endpoint_persists_stamps_officer_and_validates(maker_) -> None:
    _, plot_id = await _seed_household_with_plot(maker_)

    async with maker_() as session:
        out = await ward_watch_record_diagnosis_endpoint(
            payload=DiagnosisIn(
                plot_id=str(plot_id),
                observed_crop="maize",
                condition="pest_damage",
                cause="pest",
            ),
            principal=_OFFICER,
            session=session,
        )
        assert out.condition == "pest_damage"
        assert out.officer_id == "AGRITEX-1"  # the verified token subject, not client input
        assert out.observed_on == date.today().isoformat()

    # Unknown controlled value -> 422.
    async with maker_() as session:
        with pytest.raises(HTTPException) as exc:
            await ward_watch_record_diagnosis_endpoint(
                payload=DiagnosisIn(
                    plot_id=str(plot_id), observed_crop="maize", condition="vibes", cause="pest"
                ),
                principal=_OFFICER,
                session=session,
            )
        assert exc.value.status_code == 422

    # Unknown plot -> 404.
    async with maker_() as session:
        with pytest.raises(HTTPException) as exc:
            await ward_watch_record_diagnosis_endpoint(
                payload=DiagnosisIn(
                    plot_id=str(uuid.uuid4()),
                    observed_crop="maize",
                    condition="healthy",
                    cause="unknown",
                ),
                principal=_OFFICER,
                session=session,
            )
        assert exc.value.status_code == 404


async def test_diagnosis_endpoint_lists_labelled_set(maker_) -> None:
    _, plot_id = await _seed_household_with_plot(maker_)
    async with maker_() as session:
        await ward_watch_record_diagnosis_endpoint(
            payload=DiagnosisIn(
                plot_id=str(plot_id),
                observed_crop="maize",
                condition="nutrient_deficiency",
                cause="low_soil_fertility",
            ),
            principal=_OFFICER,
            session=session,
        )

    async with maker_() as session:
        rows = await ward_watch_diagnoses_endpoint(
            principal=_OFFICER, session=session, ward=None, limit=500
        )
    assert len(rows) == 1
    assert rows[0].cause == "low_soil_fertility"


async def test_recorded_diagnosis_appears_in_visit_package_previous_visits(maker_) -> None:
    household_id, plot_id = await _seed_household_with_plot(maker_)
    async with maker_() as session:
        await ward_watch_record_diagnosis_endpoint(
            payload=DiagnosisIn(
                plot_id=str(plot_id),
                observed_crop="maize",
                condition="waterlogging",
                cause="excess_rain",
                notes="standing water after storms",
            ),
            principal=_OFFICER,
            session=session,
        )

    async with maker_() as session:
        package = await get_household_visit_package(session, "HH-DX-1")
    assert package is not None
    assert len(package.previous_visits) == 1
    visit = package.previous_visits[0]
    assert visit.condition == "waterlogging"
    assert visit.notes == "standing water after storms"
