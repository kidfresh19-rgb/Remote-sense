"""End-to-end ingest proof for Ward Watch enrollment (backlog 0030 residual work, PRD 0003 Phase 1).

Chains the full enrollment-to-triage seam in one test, against PostGIS: a synthetic gateway batch
(`rs_sync.inbound.synthetic_declarations`) flows through `RecordingGatewayPort
.fetch_household_declarations` (zero network) -> the worker's wire-to-value mapping
(`_to_household_values`) -> `reconcile_household_declarations` (0031, folds the declared crop mix +
planting window onto a pre-seeded geometry-bearing Household/Plot) -> `ingest_household_plots` (runs
the plot's stored boundary through the mock `AccessPort`, zero network, and persists `PlotAnalysis`
rows with provenance) -> `assess_household_cohorts` (0032, the read path the officer triage queue
calls). Most of these seams already have their own unit/DB tests; this one proves they chain.

The assertion is triage VISIBILITY and correct field-folding (dominant crop, ward, Natural Region,
the low-pixel-quality flag) - not a forced quorum-met movement label. A single seeded household is
also the only member of its own cohort at every ladder rung, so it legitimately never clears the
real quorum floor (`n_min` defaults to 20); that honest `cohort_meets_quorum is False` outcome is
asserted explicitly rather than worked around with a test-only `n_min=1` override.

Needs PostGIS, so it SKIPs when no database is reachable and runs for real in CI / `docker
compose`."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from geoalchemy2.shape import from_shape
from rs_core.cohorts import CohortLevel
from rs_core.config import ImageryAdapter, Settings
from rs_core.db import Base
from rs_core.models import Household, Plot, PlotAnalysis
from rs_core.repositories import assess_household_cohorts, reconcile_household_declarations
from rs_imagery import get_access_adapter
from rs_sync import DeclarationsQuery, RecordingGatewayPort
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.worker.tasks.ward_watch import _to_household_values, ingest_household_plots

_TEST_DB_URL = os.environ.get(
    "RS_TEST_DATABASE_URL", "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
)

# HH-1001 / PL-1001-A in `synthetic_declarations()` (rs_sync/inbound.py): the fully populated
# household - crop mix maize 60% / cowpea 40%, planting_date 2025-11-20 (buckets to the MAIN
# window). These must match the fixture's client_uuid strings exactly so
# `reconcile_household_declarations` joins the wire declaration onto our seeded rows.
_HH_1001_CLIENT_UUID = uuid.UUID("11111111-1111-4111-8111-111111111111")
_PLOT_1001A_CLIENT_UUID = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")


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


async def _seed_enrolled_household(maker) -> tuple[uuid.UUID, uuid.UUID]:
    """A household enrolled offline - no `canonical_household_id` yet, since the gateway has not
    synced it back - with one officer-proxy plot, client-UUID-matched to HH-1001 / PL-1001-A so the
    inbound reconcile can join on them. Mirrors the geometry-seeding shape of
    `test_ward_watch_ingest_db._seed_household_with_plot`, plus the ward + `dominant_nr` a
    completed centroid assignment (0031, covered by its own DB test) would already have set: that
    assignment seam is not what this test proves, so ward/NR are pre-seeded directly rather than
    re-run here."""
    async with maker() as session:
        household = Household(
            client_uuid=_HH_1001_CLIENT_UUID,
            canonical_household_id=None,
            ward_name="Ward 7",
            dominant_nr="Region III",
            village="Chikomba",
            officer_id="AGRITEX-1",
        )
        session.add(household)
        await session.flush()
        plot = Plot(
            client_uuid=_PLOT_1001A_CLIENT_UUID,
            household_id=household.id,
            boundary=from_shape(_square_mp(30.0, -17.0, 0.002), srid=4326),
            geometry_source="officer_proxy",
            area_m2=5000.0,
            size_class="small_holding",
        )
        session.add(plot)
        await session.flush()
        ids = (household.id, plot.id)
        await session.commit()
        return ids


async def test_enrolled_household_flows_from_gateway_declarations_to_triage(maker_) -> None:
    """The full seam this backlog item asks for: fetch -> map -> reconcile -> ingest -> triage-read,
    chained against one pre-seeded geometry-bearing household."""
    household_id, plot_id = await _seed_enrolled_household(maker_)

    # 1) RecordingGatewayPort.fetch_household_declarations - zero network, replaying the shared
    # deterministic two-household batch (HH-1001 full, HH-1002 deliberately sparse).
    gateway = RecordingGatewayPort()
    batch = await gateway.fetch_household_declarations(DeclarationsQuery())
    assert {d.canonical_household_id for d in batch.declarations} == {"HH-1001", "HH-1002"}

    # 2) the worker's wire -> value mapping (already unit-tested alone; chained here for real).
    values = _to_household_values(batch)

    # 3) 0031 reconcile: folds the declared crop mix + planting window onto the pre-seeded rows.
    # Only HH-1001 / PL-1001-A match a row we already hold; HH-1002 (no local household) and
    # PL-1001-B (no local plot) have no counterpart and are silently skipped - `_find_household`'s
    # documented behaviour ("geometry-bearing rows arrive from enrollment, not from this read"),
    # deliberate and exercised here, not a gap.
    async with maker_() as session:
        result = await reconcile_household_declarations(session, values)
        await session.commit()
    assert (result.households, result.plots) == (1, 1)

    async with maker_() as session:
        household = (
            await session.execute(select(Household).where(Household.id == household_id))
        ).scalar_one()
        plot = (await session.execute(select(Plot).where(Plot.id == plot_id))).scalar_one()
    assert household.canonical_household_id == "HH-1001"  # set for the first time by reconcile
    assert plot.dominant_crop == "maize"  # 60% share beats cowpea's 40%
    assert plot.planting_window == "main"  # 2025-11-20 falls in the MAIN window

    # 4) 0031 ingest: the plot's stored boundary runs through the (mock, zero-network) analysis
    # engine and its index series is persisted with provenance + clear fraction + the §4 flag.
    adapter = get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))
    async with maker_() as session:
        summary = await ingest_household_plots(
            session,
            household_id,
            adapter=adapter,
            months=3,
            now=datetime(2026, 3, 1, tzinfo=UTC),
        )
    assert summary["passes_stored"] > 0

    async with maker_() as session:
        rows = (
            (await session.execute(select(PlotAnalysis).where(PlotAnalysis.plot_id == plot_id)))
            .scalars()
            .all()
        )
    ndvi_rows = [r for r in rows if r.index_name == "ndvi"]
    assert len(ndvi_rows) >= 2  # the movement lens needs >= 2 clear passes to read a trend
    assert all(r.clear_fraction >= 0.5 and r.provider == "mock" for r in ndvi_rows)

    # 5) 0032 triage read: the actual seam `/ward-watch/triage` calls under the hood. Default
    # quorum/threshold knobs - no `n_min` override - so this is the real, honest read.
    async with maker_() as session:
        assessments = await assess_household_cohorts(session, wards=None, index_name="ndvi")

    by_id = {a.household_id: a for a in assessments}
    assert "HH-1001" in by_id  # the enrolled household is triage-visible
    visible = by_id["HH-1001"]
    assert visible.dominant_crop == "maize"  # folded from the declared crop mix, not hard-coded
    assert visible.ward == "Ward 7"
    assert visible.dominant_nr == "Region III"
    assert visible.low_pixel_quality is False  # thousands of clear mock pixels, never 2-pixel
    # Honest small-cohort behaviour (not a gap): a lone household's own cohort at every available
    # ladder rung (ward+crop+window, ward+crop, natural-region+crop; no district layer seeded here)
    # has size 1, so it never clears the real quorum floor and the fallback ladder reports its
    # widest available rung rather than a falsely confident narrow one.
    assert visible.cohort_meets_quorum is False
    assert visible.cohort_level == CohortLevel.NATURAL_REGION_CROP
