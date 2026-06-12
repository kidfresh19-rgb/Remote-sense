"""COG retention (S4.3) - the missing half of risk S-1 (unbounded raster growth). D7 made raw
bands transient; this prunes the derived index COGs themselves, on two prongs:

1. **Stale geometry version.** A boundary change (DI-5) writes new COGs under the new version and
   the tiler route always carries the current one, so older-version objects are unreachable
   garbage from the moment the bump lands.
2. **Aged-out pass.** Passes older than the retention horizon roll off. The default horizon
   equals the backfill depth (`backfill_months`), so previews exist exactly for the history the
   workspace advertises; the zonal stats and provenance rows are never touched (invariant 5),
   only the preview raster goes.

The decision is pure (`select_prunable`), mirroring planning.py: the orchestrator runs the DB
query, this decides what dies. Deleting the object and NULLing `analysis.cog_uri` makes a run
idempotent and self-healing: a crash between the two converges on the next run because an
S3/MinIO delete of an absent key succeeds, and the workspace stops offering an overlay for a
pruned pass the moment `cog_uri` is NULL (the tiler's missing-COG 404 already covers stragglers).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from rs_core.models import Analysis, Field
from rs_core.storage import CogStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.worker.planning import backfill_window


@dataclass(frozen=True)
class CogRow:
    """One stored COG, as the retention query sees it: the analysis row it is stamped on, the
    field + geometry version it was computed against, the pass date, and the object key."""

    analysis_id: uuid.UUID
    field_id: uuid.UUID
    geometry_version: int
    pass_date: date
    cog_key: str


@dataclass(frozen=True)
class PruneSummary:
    examined: int
    pruned_stale_version: int
    pruned_aged_out: int

    @property
    def pruned(self) -> int:
        return self.pruned_stale_version + self.pruned_aged_out


def retention_cutoff(today: date, months: int) -> date:
    """Passes sensed before this date age out. Reuses the backfill window math so the default
    horizon and the advertised history depth can never drift apart."""
    start, _ = backfill_window(today, months)
    return start


def select_prunable(
    rows: Sequence[CogRow],
    current_versions: Mapping[uuid.UUID, int],
    *,
    cutoff: date,
) -> list[CogRow]:
    """The COGs to delete: anything at a stale geometry version, plus anything at the current
    version whose pass predates the cutoff. A field absent from `current_versions` is never
    stale-pruned (no basis for comparison); pass-date pruning still applies. Pure - the
    orchestrator runs the query, this decides."""
    prunable: list[CogRow] = []
    for row in rows:
        current = current_versions.get(row.field_id)
        stale = current is not None and row.geometry_version < current
        if stale or row.pass_date < cutoff:
            prunable.append(row)
    return prunable


async def prune_cogs(
    session: AsyncSession,
    store: CogStore,
    *,
    today: date,
    retention_months: int,
) -> PruneSummary:
    """Delete every prunable COG from the object store and NULL `cog_uri` on its analysis row.
    Runs inside the caller's transaction - the caller commits. Object deletion happens before the
    NULL so a mid-run crash leaves rows that re-prune (idempotently) on the next pass, never
    orphaned objects."""
    cutoff = retention_cutoff(today, retention_months)

    result = await session.execute(
        select(
            Analysis.id,
            Analysis.field_id,
            Analysis.geometry_version,
            Analysis.pass_date,
            Analysis.cog_uri,
        ).where(Analysis.cog_uri.is_not(None))
    )
    rows = [
        CogRow(
            analysis_id=analysis_id,
            field_id=field_id,
            geometry_version=geometry_version,
            pass_date=pass_date,
            cog_key=cog_uri,
        )
        for analysis_id, field_id, geometry_version, pass_date, cog_uri in result.all()
    ]

    versions = await session.execute(select(Field.id, Field.geometry_version))
    current_versions: dict[uuid.UUID, int] = dict(versions.all())  # type: ignore[arg-type]

    prunable = select_prunable(rows, current_versions, cutoff=cutoff)

    stale = 0
    for row in prunable:
        store.delete(row.cog_key)
        analysis = await session.get(Analysis, row.analysis_id)
        if analysis is not None:
            analysis.cog_uri = None
        current = current_versions.get(row.field_id)
        if current is not None and row.geometry_version < current:
            stale += 1

    return PruneSummary(
        examined=len(rows),
        pruned_stale_version=stale,
        pruned_aged_out=len(prunable) - stale,
    )
