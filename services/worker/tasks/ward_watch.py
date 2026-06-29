"""Ward Watch per-household ingestion (backlog 0031): run each plot's proxy geometry through the
unchanged analysis engine and persist its index series with provenance + the §4 pixel-quality flag.

This is the tracer that proves Ward Watch data flows end to end through the scientific core
(PRD 0003 §4). The triage / rollup endpoints stay empty until the movement-lens assembly (0032)
reads these stored series; this slice is the data foundation. No gateway contract is touched, and
raw bands stay transient - the engine derives the index stats and discards the arrays (invariant 7).

The task is deliberately thin (config -> NullPool session + adapter) and delegates to the injectable
async orchestrator `ingest_household_plots`, testable against the mock adapter + a real session with
zero network (CLAUDE.md §3)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from geoalchemy2.shape import to_shape
from rs_core import get_settings
from rs_core.config import aoi_pass_concurrency
from rs_core.cropmix import CropWeight
from rs_core.logging import get_logger
from rs_core.repositories import (
    HouseholdDeclarationValue,
    PlotDeclarationValue,
    reconcile_household_declarations,
    upsert_plot_analysis,
)
from rs_imagery import AccessPort
from rs_sync import HouseholdDeclarationBatch
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.worker.celery_app import celery
from services.worker.plot_persistence import plot_series_upsert_kwargs
from services.worker.tasks.analysis import _analyse_aoi_series

log = get_logger("services.worker.tasks.ward_watch")

# v1 drives the movement lens off NDVI; the orchestrator takes an index, so widening to the standard
# set later is additive.
DEFAULT_PLOT_INDEX = "ndvi"


def _clearest_per_day(passes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One ok pass per calendar day - the clearest when several scenes share a day - so the stored
    per-plot series carries a single value per (plot, index, day), matching the upsert identity."""
    best: dict[str, dict[str, Any]] = {}
    for p in passes:
        if p.get("status") != "ok":
            continue
        day = p.get("pass_date")
        if day is None:
            continue
        if day not in best or float(p.get("clear_fraction", 0.0)) > float(
            best[day].get("clear_fraction", 0.0)
        ):
            best[day] = p
    return list(best.values())


async def ingest_household_plots(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    adapter: AccessPort,
    months: int,
    index_name: str = DEFAULT_PLOT_INDEX,
    concurrency: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run every plot of one household through the AOI engine and persist its index series. Each
    plot's stored boundary is materialised as the AOI, swept over `months` of backfill, deduped to
    the clearest pass per day, and written as additive idempotent `plot_analysis` rows carrying full
    provenance, the per-AOI clear fraction, and the §4 pixel-quality flag. Injectable session +
    adapter so it runs against the mock adapter with zero network (CLAUDE.md §3). Returns a small
    summary `{household_id, plots, passes_stored}`."""
    from rs_core.models import Plot

    plots = (
        (await session.execute(select(Plot).where(Plot.household_id == household_id)))
        .scalars()
        .all()
    )
    passes_stored = 0
    for plot in plots:
        if plot.boundary is None:
            continue
        geometry = dict(mapping(to_shape(plot.boundary)))
        series = await _analyse_aoi_series(
            geometry,
            index_name,
            "backfill",
            None,
            months,
            adapter=adapter,
            backfill_months=months,
            concurrency=concurrency,
            now=now,
        )
        for pass_result in _clearest_per_day(series.get("passes", [])):
            await upsert_plot_analysis(
                session, **plot_series_upsert_kwargs(pass_result, plot_id=plot.id)
            )
            passes_stored += 1
    await session.commit()
    log.info(
        "ward_watch.ingest.complete",
        household_id=str(household_id),
        plots=len(plots),
        passes_stored=passes_stored,
    )
    return {"household_id": str(household_id), "plots": len(plots), "passes_stored": passes_stored}


@celery.task(name="ward_watch.ingest_household_plots")
def ingest_household_plots_task(household_id: str, months: int | None = None) -> dict[str, Any]:
    """Celery wrapper: a per-task NullPool engine + the configured imagery adapter, then
    `ingest_household_plots`. A forked worker never shares an async pool across loops. Never touches
    the frozen gateway contract."""
    settings = get_settings()

    async def _run() -> dict[str, Any]:
        from rs_imagery import get_access_adapter
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        resolved_months = months or settings.backfill_months
        concurrency = aoi_pass_concurrency(settings)
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        adapter = get_access_adapter(settings)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await ingest_household_plots(
                    session,
                    uuid.UUID(household_id),
                    adapter=adapter,
                    months=resolved_months,
                    concurrency=concurrency,
                )
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _to_household_values(batch: HouseholdDeclarationBatch) -> list[HouseholdDeclarationValue]:
    """Map the rs_sync inbound wire models (0026) onto the rs_core value types the reconcile takes -
    the worker is the only layer that knows both. Tolerant: a non-UUID client id or a weightless
    declared crop is dropped here rather than raised downstream."""
    households: list[HouseholdDeclarationValue] = []
    for hh in batch.declarations:
        plots: list[PlotDeclarationValue] = []
        for plot in hh.plots:
            weighted: list[CropWeight] = []
            for c in plot.crop_mix:
                if c.weight_pct is None:
                    continue
                weighted.append(CropWeight(crop=c.crop, weight_pct=c.weight_pct))
            crop_mix = tuple(weighted)
            planting_date = plot.planting.planting_date if plot.planting is not None else None
            plots.append(
                PlotDeclarationValue(
                    client_uuid=_parse_uuid(plot.client_uuid),
                    crop_mix=crop_mix,
                    planting_date=planting_date,
                )
            )
        households.append(
            HouseholdDeclarationValue(
                canonical_household_id=hh.canonical_household_id,
                client_uuid=_parse_uuid(hh.client_uuid),
                plots=tuple(plots),
            )
        )
    return households


@celery.task(name="ward_watch.reconcile_ward_declarations")
def reconcile_ward_declarations_task(ward: str | None = None) -> dict[str, Any]:
    """Fetch a ward's household declarations from the gateway (0026 inbound contract, read-only) and
    fold them onto the households / plots we hold (0031): canonical id, dominant crop, planting
    window. Returns `{households, plots}` reconciled. Never mutates the gateway."""
    settings = get_settings()

    async def _run() -> dict[str, Any]:
        from rs_sync import DeclarationsQuery
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from services.worker.publish import gateway_from_settings

        gateway = gateway_from_settings(settings)
        batch = await gateway.fetch_household_declarations(DeclarationsQuery(ward_name=ward))
        values = _to_household_values(batch)
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                result = await reconcile_household_declarations(session, values)
                await session.commit()
                return {"households": result.households, "plots": result.plots}
        finally:
            await engine.dispose()

    return asyncio.run(_run())
