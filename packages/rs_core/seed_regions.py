"""Seed the Natural Region layer at init (PRD 0002 slice 1). Run once after `alembic upgrade head`
by the compose `seed` service. Idempotent on (source, year, version): re-running is a no-op,
and the authoritative ZINGSA AEZ 2020 map supersedes the current candidate simply by carrying a new
version string. After seeding it recomputes every existing farm's region assignment so a stack that
already ingested farms lands them in their regions immediately.

⚑ CONFIRM: the seed file is a NON-AUTHORITATIVE candidate (see data/natural_regions/README.md).
Replace it with the authoritative ZINGSA AEZ 2020 file and re-run; the new version supersedes it."""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rs_core.config import get_settings
from rs_core.logging import get_logger
from rs_core.regions import read_region_layer
from rs_core.repositories.regions import (
    recompute_farm_region_assignments,
    seed_natural_regions,
)

log = get_logger("rs_core.seed_regions")

# Candidate provenance (override recorded 2026-06-18). The authoritative ZINGSA values are held in
# docs/plan/0002-natural-region-foundation-prep.md; swap them in when the real file lands.
_LAYER_NAME = "Zimbabwe Natural Regions (candidate)"
_LAYER_SOURCE = "candidate (non-authoritative), pending ZINGSA AEZ 2020"
_LAYER_YEAR = 2020
_LAYER_VERSION = "candidate-2026-06-18"
_LAYER_AUTHORITY = "UNVERIFIED, replace with Government of Zimbabwe / ZINGSA on authoritative seed"
_LAYER_CITATION = (
    "Candidate Agroecological Zones of Zimbabwe; replace with Manatsa et al. (2020), "
    "Revision of Zimbabwe's Agro-Ecological Zones (ZINGSA AEZ 2020)."
)


async def _seed() -> None:
    settings = get_settings()
    loaded = read_region_layer(
        settings.natural_region_seed_path, name_column=settings.natural_region_name_column
    )
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            layer = await seed_natural_regions(
                session,
                name=_LAYER_NAME,
                source=_LAYER_SOURCE,
                year=_LAYER_YEAR,
                version=_LAYER_VERSION,
                crs=loaded.source_crs,
                features=loaded.features,
                publishing_authority=_LAYER_AUTHORITY,
                citation=_LAYER_CITATION,
                naming_column=settings.natural_region_name_column,
                acquisition_date=date(2026, 6, 18),
                acquisition_path="user-provided candidate",
                file_path=settings.natural_region_seed_path,
            )
            written = await recompute_farm_region_assignments(
                session, edge_tolerance_m=settings.region_boundary_adjacent_tolerance_m
            )
            await session.commit()
    finally:
        await engine.dispose()

    log.info(
        "natural_regions.seeded",
        layer_id=str(layer.id),
        version=_LAYER_VERSION,
        regions=len(loaded.features),
        skipped=len(loaded.skipped),
        assignments_written=written,
    )


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
