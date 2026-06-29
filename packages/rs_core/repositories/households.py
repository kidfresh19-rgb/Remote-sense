"""Reconcile gateway-declared household data onto our enrollment rows (backlog 0031).

The gateway is the identity authority (invariant 6): officer enrollment syncs to it and we read the
declarations back (the 0026 inbound contract). This module folds those declarations onto the
`Household` / `Plot` / `CropMixEntry` rows we already hold - it sets the canonical join id, resolves
the declared intercrop mix to a dominant crop (`rs_core.cropmix`), and buckets the declared planting
date into a window (`rs_core.strata`).

VALUE-BASED: the worker maps the rs_sync wire models onto these plain types, so rs_core keeps no
upward dependency on the sync layer (the same boundary `rs_sync.payload` keeps in reverse). Tolerant
(receiver-tolerance rule): a household or plot we do not yet hold is skipped, an empty mix or absent
planting date leaves the stored field untouched, and a malformed declared mix is logged and left
as-is - never a hard failure that sinks ingest."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.cropmix import CropWeight, resolve_crop_mix
from rs_core.logging import get_logger
from rs_core.models import CropMixEntry, Household, Plot
from rs_core.strata import bucket_planting_window

log = get_logger("rs_core.repositories.households")


@dataclass(frozen=True)
class PlotDeclarationValue:
    """One plot's gateway-declared data. Plots join on `client_uuid` (the offline enrollment id;
    `Plot` holds no gateway plot id). `crop_mix` carries only entries that have a weight, since
    `resolve_crop_mix` needs shares; an empty mix or a None `planting_date` means the gateway holds
    no value and the stored field is left untouched."""

    client_uuid: uuid.UUID | None = None
    crop_mix: tuple[CropWeight, ...] = ()
    planting_date: date | None = None


@dataclass(frozen=True)
class HouseholdDeclarationValue:
    """One household's declarations, joined on `canonical_household_id` then `client_uuid`."""

    canonical_household_id: str
    client_uuid: uuid.UUID | None = None
    plots: tuple[PlotDeclarationValue, ...] = ()


@dataclass
class ReconcileResult:
    """How many households / plots the reconcile actually touched."""

    households: int = 0
    plots: int = 0


async def _find_household(
    session: AsyncSession, decl: HouseholdDeclarationValue
) -> Household | None:
    """Match by the canonical join id first, then by the offline client id. None when we hold no
    such household yet (geometry-bearing rows arrive from enrollment, not from this read)."""
    if decl.canonical_household_id:
        household = (
            await session.execute(
                select(Household).where(
                    Household.canonical_household_id == decl.canonical_household_id
                )
            )
        ).scalar_one_or_none()
        if household is not None:
            return household
    if decl.client_uuid is not None:
        return (
            await session.execute(
                select(Household).where(Household.client_uuid == decl.client_uuid)
            )
        ).scalar_one_or_none()
    return None


async def _apply_plot_declaration(
    session: AsyncSession, plot: Plot, pdecl: PlotDeclarationValue
) -> bool:
    """Fold one plot's declared crop mix + planting onto the stored row. Returns whether anything
    changed. A malformed mix (unknown crop, shares not summing to ~100) is logged and skipped, so
    the rest of the reconcile proceeds."""
    changed = False
    if pdecl.crop_mix:
        try:
            resolved = resolve_crop_mix(list(pdecl.crop_mix))
        except ValueError as exc:
            log.warning("ward_watch.reconcile.bad_crop_mix", plot_id=str(plot.id), detail=str(exc))
            resolved = None
        if resolved is not None:
            await session.execute(delete(CropMixEntry).where(CropMixEntry.plot_id == plot.id))
            await session.flush()
            session.add_all(
                [
                    CropMixEntry(plot_id=plot.id, crop=entry.crop, weight_pct=entry.weight_pct)
                    for entry in resolved.entries
                ]
            )
            plot.dominant_crop = resolved.dominant_crop
            changed = True
    if pdecl.planting_date is not None:
        plot.planting_date = pdecl.planting_date
        plot.planting_window = bucket_planting_window(pdecl.planting_date).value
        changed = True
    return changed


async def reconcile_household_declarations(
    session: AsyncSession, declarations: Sequence[HouseholdDeclarationValue]
) -> ReconcileResult:
    """Fold gateway declarations onto the households / plots we hold: set the canonical join id, and
    resolve each plot's declared crop mix -> `dominant_crop` and planting date -> `planting_window`.
    Idempotent and tolerant - re-running converges, and a household / plot we do not hold is
    skipped. Does not commit; the caller owns the transaction."""
    result = ReconcileResult()
    for decl in declarations:
        household = await _find_household(session, decl)
        if household is None:
            continue
        if household.canonical_household_id is None:
            household.canonical_household_id = decl.canonical_household_id
        result.households += 1

        plots = (
            (await session.execute(select(Plot).where(Plot.household_id == household.id)))
            .scalars()
            .all()
        )
        by_client = {p.client_uuid: p for p in plots}
        for pdecl in decl.plots:
            if pdecl.client_uuid is None:
                continue
            plot = by_client.get(pdecl.client_uuid)
            if plot is None:
                continue
            if await _apply_plot_declaration(session, plot, pdecl):
                result.plots += 1
    await session.flush()
    return result
