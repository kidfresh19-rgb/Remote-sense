"""One-time admin script: enqueue backfill_rgb_cog for all (field_id, scene_id, geometry_version)
tuples that have index COGs but no rgb.tif (backlog 0024).

Run inside the container (or with the full geo/storage extras installed) where both the DB and the
COG store are reachable:

    python scripts/enqueue_rgb_backfill.py [--dry-run] [--limit N]

Flags:
  --dry-run   Print what would be enqueued but do not send any Celery tasks.
  --limit N   Only enqueue at most N tasks (useful for a staged roll-out).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rs_core import Analysis, cog_key, cog_store_from_settings, get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


async def _collect_missing(settings, store, limit: int | None) -> list[dict]:
    """Query the DB for passes that have at least one index row but no rgb.tif COG."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Analysis.field_id,
                        Analysis.scene_id,
                        Analysis.geometry_version,
                        Analysis.pass_date,
                    ).distinct()
                )
            ).all()
    finally:
        await engine.dispose()

    missing = []
    for field_id, scene_id, geometry_version, pass_date in rows:
        key = cog_key(
            field_id=field_id,
            scene_id=scene_id,
            index="rgb",
            geometry_version=geometry_version,
        )
        if not store.exists(key):
            missing.append(
                {
                    "field_id": str(field_id),
                    "scene_id": scene_id,
                    "geometry_version": geometry_version,
                    "pass_date": pass_date.isoformat(),
                }
            )
        if limit is not None and len(missing) >= limit:
            break

    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue RGB COG backfill tasks")
    parser.add_argument("--dry-run", action="store_true", help="Print tasks without enqueuing")
    parser.add_argument("--limit", type=int, default=None, help="Max tasks to enqueue")
    args = parser.parse_args()

    settings = get_settings()
    store = cog_store_from_settings(settings)
    if store is None:
        print("ERROR: COG store not available (boto3 missing or S3 misconfigured)", file=sys.stderr)
        sys.exit(1)

    missing = asyncio.run(_collect_missing(settings, store, args.limit))
    print(f"Found {len(missing)} passes missing rgb.tif")

    if args.dry_run:
        for item in missing:
            print(
                f"  DRY-RUN  field={item['field_id']}  scene={item['scene_id']}"
                f"  gv={item['geometry_version']}  date={item['pass_date']}"
            )
        return

    from services.worker.tasks.backfill_rgb import backfill_rgb_cog

    enqueued = 0
    for item in missing:
        backfill_rgb_cog.apply_async(
            args=[item["field_id"], item["scene_id"], item["geometry_version"], item["pass_date"]],
        )
        enqueued += 1

    print(f"Enqueued {enqueued} backfill_rgb_cog tasks on the default (bulk) queue")


if __name__ == "__main__":
    main()
