"""Ward Watch per-household ingestion against PostGIS (backlog 0031).

A household's plot runs through the unchanged analysis engine (mock adapter, zero network) and its
index series is stored with the clear fraction + full provenance (AC1); a low-pixel pass is flagged
and the upsert is idempotent (AC2, §4); gateway declarations reconcile onto the household (canonical
id, dominant crop, planting window); and centroid assignment places the household in its ward +
Natural Region. These need PostGIS, so they SKIP when no database is reachable and run for real in
CI / `docker compose`."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.config import ImageryAdapter, Settings
from rs_core.cropmix import CropWeight
from rs_core.db import Base
from rs_core.models import CropMixEntry, Household, Plot, PlotAnalysis
from rs_core.regions import ParsedRegionFeature
from rs_core.repositories import (
    HouseholdDeclarationValue,
    PlotDeclarationValue,
    assign_households_by_centroid,
    reconcile_household_declarations,
    upsert_plot_analysis,
)
from rs_core.repositories.regions import seed_natural_regions, seed_ward_boundaries
from rs_imagery import get_access_adapter
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.worker.plot_persistence import plot_series_upsert_kwargs
from services.worker.tasks.ward_watch import ingest_household_plots

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)
_TOL = 50.0


def _rect_mp(minx: float, miny: float, maxx: float, maxy: float) -> MultiPolygon:
    return MultiPolygon(
        [Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)])]
    )


def _square_mp(lon: float, lat: float, side: float) -> MultiPolygon:
    h = side / 2
    return MultiPolygon(
        [Polygon([(lon - h, lat - h), (lon + h, lat - h), (lon + h, lat + h), (lon - h, lat + h)])]
    )


def _ok_pass(pass_date: str, *, clear: float, pixels: int) -> dict[str, Any]:
    return {
        "status": "ok",
        "index": "ndvi",
        "pass_date": pass_date,
        "scene_id": f"S2_{pass_date}",
        "formula_version": "ndvi-v1",
        "provider": "mock",
        "provider_scene_id": f"prov_{pass_date}",
        "processing_mode": "mock",
        "mean": 0.5,
        "min": 0.1,
        "max": 0.9,
        "p10": 0.2,
        "p90": 0.8,
        "clear_fraction": clear,
        "confidence": "high",
        "resolution_m": 10.0,
        "pixels": pixels,
    }


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


async def _seed_household_with_plot(
    maker, *, canonical: str | None = "HH-ING-1", lon: float = 30.0, lat: float = -17.0
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A household with one officer-proxy plot. Returns (household_id, plot_id, household
    client_uuid, plot client_uuid) so reconcile tests can join on the client ids."""
    async with maker() as session:
        household = Household(
            client_uuid=uuid.uuid4(),
            canonical_household_id=canonical,
            ward_name="Ward 7",
            officer_id="AGRITEX-1",
        )
        session.add(household)
        await session.flush()
        plot = Plot(
            client_uuid=uuid.uuid4(),
            household_id=household.id,
            boundary=from_shape(_square_mp(lon, lat, 0.002), srid=4326),
            geometry_source="officer_proxy",
            area_m2=5000.0,
            size_class="small_holding",
        )
        session.add(plot)
        await session.flush()
        ids = (household.id, plot.id, household.client_uuid, plot.client_uuid)
        await session.commit()
        return ids


async def test_ingest_stores_series_with_clear_fraction_and_provenance(maker_) -> None:
    household_id, plot_id, _, _ = await _seed_household_with_plot(maker_)
    adapter = get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))

    async with maker_() as session:
        summary = await ingest_household_plots(
            session,
            household_id,
            adapter=adapter,
            months=3,
            now=datetime(2025, 6, 15, tzinfo=UTC),
        )
    assert summary["plots"] == 1
    assert summary["passes_stored"] > 0

    async with maker_() as session:
        rows = (
            (await session.execute(select(PlotAnalysis).where(PlotAnalysis.plot_id == plot_id)))
            .scalars()
            .all()
        )
        assert rows, "expected a stored per-plot index series"
        for row in rows:
            assert row.index_name == "ndvi"
            assert 0.0 <= row.clear_fraction <= 1.0
            assert row.provider and row.provider_scene_id and row.processing_mode
            assert row.formula_version
            assert row.pixels > 0
        # one value per (plot, index, day): no duplicate pass dates survive the dedupe
        dates = [r.pass_date for r in rows]
        assert len(dates) == len(set(dates))


async def test_low_pixel_pass_is_flagged_and_upsert_is_idempotent(maker_) -> None:
    _, plot_id, _, _ = await _seed_household_with_plot(maker_)

    async with maker_() as session:
        row, created = await upsert_plot_analysis(
            session,
            **plot_series_upsert_kwargs(
                _ok_pass("2025-02-01", clear=0.9, pixels=2), plot_id=plot_id
            ),
        )
        await session.commit()
        assert created is True
        assert row.pixels == 2
        assert row.low_pixel_quality is True  # §4: a 2-pixel plot is never shown as confident

    async with maker_() as session:
        _, created_again = await upsert_plot_analysis(
            session,
            **plot_series_upsert_kwargs(
                _ok_pass("2025-02-01", clear=0.95, pixels=2), plot_id=plot_id
            ),
        )
        await session.commit()
        assert created_again is False  # same identity refreshes in place
        rows = (
            (await session.execute(select(PlotAnalysis).where(PlotAnalysis.plot_id == plot_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].clear_fraction == 0.95  # refreshed, not duplicated


async def test_reconcile_sets_canonical_id_dominant_crop_and_window(maker_) -> None:
    household_id, plot_id, h_client, p_client = await _seed_household_with_plot(
        maker_, canonical=None
    )
    value = HouseholdDeclarationValue(
        canonical_household_id="HH-RC-1",
        client_uuid=h_client,
        plots=(
            PlotDeclarationValue(
                client_uuid=p_client,
                crop_mix=(CropWeight("maize", 60.0), CropWeight("cowpea", 40.0)),
                planting_date=date(2025, 11, 20),
            ),
        ),
    )

    async with maker_() as session:
        result = await reconcile_household_declarations(session, [value])
        await session.commit()
        assert (result.households, result.plots) == (1, 1)

    async with maker_() as session:
        household = (
            await session.execute(select(Household).where(Household.id == household_id))
        ).scalar_one()
        plot = (await session.execute(select(Plot).where(Plot.id == plot_id))).scalar_one()
        entries = (
            (await session.execute(select(CropMixEntry).where(CropMixEntry.plot_id == plot_id)))
            .scalars()
            .all()
        )
        assert household.canonical_household_id == "HH-RC-1"
        assert plot.dominant_crop == "maize"
        assert plot.planting_window == "main"  # 20 Nov falls in the MAIN window
        assert {e.crop for e in entries} == {"maize", "cowpea"}


async def test_centroid_assignment_sets_ward_and_natural_region(maker_) -> None:
    async with maker_() as session:
        nr_layer = await seed_natural_regions(
            session,
            name="NR (test)",
            source="test-nr",
            year=2020,
            version="v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature("Region III", _rect_mp(29.0, -18.0, 33.0, -16.0), "EPSG:4326")
            ],
        )
        ward_layer = await seed_ward_boundaries(
            session,
            name="Ward (test)",
            source="test-ward",
            year=2020,
            version="v1",
            crs="EPSG:4326",
            features=[
                ParsedRegionFeature("Ward 7", _rect_mp(29.0, -18.0, 33.0, -16.0), "EPSG:4326")
            ],
        )
        household = Household(client_uuid=uuid.uuid4(), ward_name=None)
        session.add(household)
        await session.flush()
        plot = Plot(
            client_uuid=uuid.uuid4(),
            household_id=household.id,
            boundary=from_shape(_square_mp(30.0, -17.0, 0.002), srid=4326),
            geometry_source="officer_proxy",
        )
        session.add(plot)
        await session.flush()
        nr_id, ward_id, household_id = nr_layer.id, ward_layer.id, household.id
        await session.commit()

    async with maker_() as session:
        written = await assign_households_by_centroid(
            session, ward_layer_id=ward_id, nr_layer_id=nr_id, edge_tolerance_m=_TOL
        )
        await session.commit()
        assert written == 1

    async with maker_() as session:
        household = (
            await session.execute(select(Household).where(Household.id == household_id))
        ).scalar_one()
        assert household.dominant_nr == "Region III"
        assert household.ward_boundary_id is not None
        assert household.ward_name == "Ward 7"  # filled because it was unset (not clobbered)
