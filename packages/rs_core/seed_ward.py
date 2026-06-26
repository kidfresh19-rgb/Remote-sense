"""Seed the ward administrative boundary layer at init (Ward Watch 0027). Mirrors seed_regions.py.
Run once after `alembic upgrade head` by the compose `seed` service. Idempotent on
(source, year, version): re-running is a no-op; a new version string supersedes the candidate.
After seeding it assigns every household with a non-null ward_name to its matching boundary so an
existing stack is ready immediately.

⚑ CONFIRM (backlog 0027 / PRD 0003 §12.6): the seed file is a NON-AUTHORITATIVE candidate,
pending procurement of the authoritative ZimStat / Surveyor-General ward boundary file.
Replace data/ward_boundaries/<candidate>.geojson with the authoritative file and re-run
with a new version string; the new layer supersedes the candidate."""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rs_core.config import get_settings
from rs_core.logging import get_logger
from rs_core.regions import read_region_layer
from rs_core.repositories.regions import (
    assign_households_to_ward_by_name,
    seed_ward_boundaries,
)

log = get_logger("rs_core.seed_ward")

# ⚑ CONFIRM (backlog 0027): replace with authoritative ZimStat provenance on arrival.
_LAYER_NAME = "Zimbabwe Ward Administrative Boundaries (candidate)"
_LAYER_SOURCE = "candidate (non-authoritative), pending ZimStat / Surveyor-General"
_LAYER_YEAR = 2023
_LAYER_VERSION = "candidate-2026-06-26"
_LAYER_AUTHORITY = "UNVERIFIED, replace with Government of Zimbabwe / ZimStat on authoritative seed"
_LAYER_CITATION = (
    "Candidate ward administrative boundaries of Zimbabwe; replace with authoritative "
    "ZimStat / Surveyor-General delimitation."
)
_NAME_COLUMN = "ward_name"


async def _seed() -> None:
    settings = get_settings()
    ward_seed_path = getattr(settings, "ward_boundary_seed_path", None)
    if ward_seed_path is None:
        log.warning(
            "ward_boundary_seed_path not set in config — skipping ward boundary seed. "
            "Set RS_WARD_BOUNDARY_SEED_PATH in .env to enable."
        )
        return

    loaded = read_region_layer(ward_seed_path, name_column=_NAME_COLUMN)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            layer = await seed_ward_boundaries(
                session,
                name=_LAYER_NAME,
                source=_LAYER_SOURCE,
                year=_LAYER_YEAR,
                version=_LAYER_VERSION,
                crs=loaded.source_crs,
                features=loaded.features,
                publishing_authority=_LAYER_AUTHORITY,
                citation=_LAYER_CITATION,
                naming_column=_NAME_COLUMN,
                acquisition_date=date(2026, 6, 26),
            )
            assigned = await assign_households_to_ward_by_name(session, layer_id=layer.id)
            await session.commit()
            log.info(
                "ward_boundary_seed_complete",
                layer_id=str(layer.id),
                boundaries=len(loaded.features),
                households_assigned=assigned,
            )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
