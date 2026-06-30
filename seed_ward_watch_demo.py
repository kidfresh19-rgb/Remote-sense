"""Seed demo Ward Watch data into the local database for a cockpit walkthrough.

Idempotent: clears any prior ``DEMO-*`` households first, then seeds one AGRITEX officer (``OFF-1``)
covering two wards with a deliberate mix of movement labels - an idiosyncratic household (falling
while its cohort holds), a systemic cohort (a whole cohort falling together), and nominal neighbours
- plus a SECOND officer's ward that ``OFF-1`` must not see, so the server-side ward-scoping (backlog
0041) is visible in the queue. Cohorts are small on purpose, so the section-4 small-cohort honesty
flag shows too.

Reads the database DSN from ``.env`` (``RS_DATABASE_URL``); run with the project ``.venv`` AFTER the
stack is up (``python start.py``), so the schema already exists. Safe to re-run.

    .venv/Scripts/python.exe seed_ward_watch_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, timedelta

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "packages"))

DEMO_PREFIX = "DEMO-"
DECLINING = [0.63, 0.61, 0.46, 0.41]
STABLE = [0.60, 0.62, 0.61, 0.60]

# (canonical, ward, officer, crop, series, lon) - the cohort key is (crop, NR, ward, window); these
# share NR "Region III" and window "main", so households in the same ward+crop form one cohort.
DEMO_HOUSEHOLDS = [
    # Ward 12 (OFF-1), maize cohort: one falling household among stable neighbours -> idiosyncratic.
    ("DEMO-W12-01", "Ward 12", "OFF-1", "maize", DECLINING, 31.00),
    ("DEMO-W12-02", "Ward 12", "OFF-1", "maize", STABLE, 31.01),
    ("DEMO-W12-03", "Ward 12", "OFF-1", "maize", STABLE, 31.02),
    ("DEMO-W12-04", "Ward 12", "OFF-1", "maize", STABLE, 31.03),
    # Ward 15 (OFF-1), sorghum cohort: the whole cohort falls together -> systemic (food-security).
    ("DEMO-W15-01", "Ward 15", "OFF-1", "sorghum", DECLINING, 31.10),
    ("DEMO-W15-02", "Ward 15", "OFF-1", "sorghum", DECLINING, 31.11),
    ("DEMO-W15-03", "Ward 15", "OFF-1", "sorghum", DECLINING, 31.12),
    # Ward 20 (OFF-2): a different officer's ward - OFF-1 must NOT see these in the queue.
    ("DEMO-W20-01", "Ward 20", "OFF-2", "maize", DECLINING, 31.20),
    ("DEMO-W20-02", "Ward 20", "OFF-2", "maize", STABLE, 31.21),
]


async def amain() -> None:
    from geoalchemy2.shape import from_shape
    from shapely.geometry import MultiPolygon, Polygon
    from sqlalchemy import delete, select, text

    from rs_core.config import get_settings
    from rs_core.db import Base, get_engine, get_sessionmaker
    from rs_core.models import Diagnosis, Household, Plot, PlotAnalysis

    print(f"Seeding demo Ward Watch data into: {get_settings().database_url}")

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)  # checkfirst: a no-op once migrated

    def square(lon: float, lat: float = -17.8) -> MultiPolygon:
        h = 0.001
        return MultiPolygon(
            [Polygon([(lon - h, lat - h), (lon + h, lat - h), (lon + h, lat + h), (lon - h, lat + h)])]
        )

    maker = get_sessionmaker()
    async with maker() as s:
        demo_hh = select(Household.id).where(Household.canonical_household_id.like(f"{DEMO_PREFIX}%"))
        demo_plots = select(Plot.id).where(Plot.household_id.in_(demo_hh))
        await s.execute(delete(PlotAnalysis).where(PlotAnalysis.plot_id.in_(demo_plots)))
        await s.execute(delete(Diagnosis).where(Diagnosis.plot_id.in_(demo_plots)))
        await s.execute(delete(Plot).where(Plot.household_id.in_(demo_hh)))
        await s.execute(delete(Household).where(Household.canonical_household_id.like(f"{DEMO_PREFIX}%")))
        await s.flush()

        start = date.today() - timedelta(days=40)
        for canonical, ward, officer, crop, series, lon in DEMO_HOUSEHOLDS:
            hh = Household(
                client_uuid=uuid.uuid4(),
                canonical_household_id=canonical,
                ward_name=ward,
                dominant_nr="Region III",
                village="Chivhu",
                officer_id=officer,
            )
            s.add(hh)
            await s.flush()
            plot = Plot(
                client_uuid=uuid.uuid4(),
                household_id=hh.id,
                boundary=from_shape(square(lon), srid=4326),
                geometry_source="officer_proxy",
                size_class="small_holding",
                dominant_crop=crop,
                planting_window="main",
            )
            s.add(plot)
            await s.flush()
            for i, value in enumerate(series):
                s.add(
                    PlotAnalysis(
                        plot_id=plot.id,
                        scene_id=f"S2_{canonical}_{i}",
                        pass_date=start + timedelta(days=10 * i),
                        index_name="ndvi",
                        mean=value,
                        clear_fraction=0.92,
                        resolution_m=10.0,
                        pixels=14,
                        low_pixel_quality=False,
                        formula_version="ndvi-v1",
                        provider="mock",
                        provider_scene_id=f"prov_{canonical}_{i}",
                        processing_mode="mock",
                    )
                )
        await s.commit()

    await engine.dispose()
    print(f"Seeded {len(DEMO_HOUSEHOLDS)} demo households (OFF-1 covers Ward 12 + Ward 15).")


if __name__ == "__main__":
    asyncio.run(amain())
