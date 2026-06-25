"""The persisted region-cluster read path (comparison engine, PRD 0002 slice 2a / backlog 0003):
assemble a cluster's members and their reference-pass health, then rank each member in the group.

Health is the one farm-analytics definition (area-weighted, crop-aware NDVI -> `classify` ->
`vigour_to_status`), computed here and handed to the pure selectors in `rs_core.comparison`, so a
cluster member's health for a farm and pass matches what the farm analytics report for the same farm
and pass. A cluster is identified by its `region_boundary_id`; members are the farms whose centroid
assignment lands in that boundary (`FarmRegionAssignment`), joined on the canonical farm id
(invariant 6). `rs_interpret` is imported inside the function, never at module level, so rs_core
keeps no import-time upward dependency on the interpretation layer."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.comparison import (
    DEFAULT_MIN_COHORT,
    DEFAULT_QUORUM,
    DEFAULT_WINDOW_DAYS,
    CropStanding,
    GroupReferencePass,
    MemberPass,
    compute_standing,
    select_group_reference_pass,
)
from rs_core.models import Analysis, Farm, FarmRegionAssignment, Field, FieldGeometryVersion

# The per-AOI clear-fraction floor below which a pass is not a reliable signal (mirrors the as-of
# view's default, S3.1). A farm's area-weighted clear fraction on a date must meet this to count as
# a clear read for the group reference pass.
DEFAULT_CLEAR_FLOOR = 0.5


@dataclass(frozen=True)
class ClusterMemberStanding:
    """One contributing member at the group reference pass: its nearest clear pass (with the signed
    day offset from the reference date), its overall area-weighted health value and crop-aware
    status, and its crop-stratified standing within the group."""

    member_id: str
    pass_date: date
    day_offset: int
    value: float
    status: str
    standings: list[CropStanding]


@dataclass(frozen=True)
class ClusterStats:
    """A region cluster's read-side health: the total membership, the selected group reference pass
    (None when no date reaches quorum), and each contributing member's reference-pass standing."""

    region_boundary_id: uuid.UUID
    index_name: str
    members_total: int
    reference: GroupReferencePass | None
    members: list[ClusterMemberStanding]


@dataclass
class _Acc:
    """A running area-weighted accumulator for one (farm, pass) over all its fields."""

    weighted_value: float = 0.0
    weighted_clear: float = 0.0
    area: float = 0.0


@dataclass
class _CropAcc:
    """A running area-weighted accumulator for one (farm, pass, crop)."""

    weighted_value: float = 0.0
    area: float = 0.0


async def _cluster_member_ids(session: AsyncSession, *, region_boundary_id: uuid.UUID) -> list[str]:
    """The canonical farm ids assigned to a cluster's boundary, sorted for a deterministic read."""
    rows = (
        (
            await session.execute(
                select(FarmRegionAssignment.canonical_farm_id).where(
                    FarmRegionAssignment.region_boundary_id == region_boundary_id
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(set(rows))


async def _cached_cluster_stats(
    session: AsyncSession, *, region_boundary_id: uuid.UUID, index_name: str
) -> ClusterStats | None:
    """The materialization seam (PRD 0002 slice 2a): in v1 there is no cache table, so this always
    misses and live compute runs. When a materialized group-stats cache lands, this is the single
    place it is read - the call site never changes."""
    return None


async def get_cluster_stats(
    session: AsyncSession,
    *,
    region_boundary_id: uuid.UUID,
    index_name: str = "ndvi",
    quorum: float = DEFAULT_QUORUM,
    window_days: int = DEFAULT_WINDOW_DAYS,
    clear_floor: float = DEFAULT_CLEAR_FLOOR,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> ClusterStats:
    """A region cluster's reference-pass health. Checks the (always-empty in v1) cache, then falls
    back to live compute - the seam where a materialized cache slots in without touching callers.
    `quorum`, `window_days`, `clear_floor`, and `min_cohort` are all configurable (rs_core.config),
    never hard-coded into the selection."""
    cached = await _cached_cluster_stats(
        session, region_boundary_id=region_boundary_id, index_name=index_name
    )
    if cached is not None:
        return cached
    return await _compute_cluster_stats(
        session,
        region_boundary_id=region_boundary_id,
        index_name=index_name,
        quorum=quorum,
        window_days=window_days,
        clear_floor=clear_floor,
        min_cohort=min_cohort,
    )


async def _compute_cluster_stats(
    session: AsyncSession,
    *,
    region_boundary_id: uuid.UUID,
    index_name: str,
    quorum: float,
    window_days: int,
    clear_floor: float,
    min_cohort: int,
) -> ClusterStats:
    """Live compute: assemble each member's clear-pass series (area-weighted, crop-aware), select
    the group reference pass, then rank every contributing member within the group at that pass."""
    member_ids = await _cluster_member_ids(session, region_boundary_id=region_boundary_id)
    if not member_ids:
        return ClusterStats(
            region_boundary_id=region_boundary_id,
            index_name=index_name,
            members_total=0,
            reference=None,
            members=[],
        )

    rows = (
        await session.execute(
            select(
                Farm.canonical_farm_id,
                Field.crop,
                FieldGeometryVersion.area_m2,
                Analysis.pass_date,
                Analysis.mean,
                Analysis.clear_fraction,
            )
            .join(Field, Field.farm_id == Farm.id)
            .join(Analysis, Analysis.field_id == Field.id)
            .outerjoin(
                FieldGeometryVersion,
                (FieldGeometryVersion.field_id == Field.id)
                & (FieldGeometryVersion.version == Field.geometry_version),
            )
            .where(
                Farm.canonical_farm_id.in_(member_ids),
                Analysis.index_name == index_name,
            )
        )
    ).all()

    overall: dict[tuple[str, date], _Acc] = defaultdict(_Acc)
    per_crop: dict[tuple[str, date], dict[str, _CropAcc]] = defaultdict(dict)
    for canonical_farm_id, crop, area_m2, pass_date, mean, clear_fraction in rows:
        if mean is None:
            continue
        area = area_m2 if area_m2 is not None else 1.0
        acc = overall[(canonical_farm_id, pass_date)]
        acc.weighted_value += mean * area
        acc.weighted_clear += clear_fraction * area
        acc.area += area
        if crop:
            crops = per_crop[(canonical_farm_id, pass_date)]
            crop_acc = crops.setdefault(crop, _CropAcc())
            crop_acc.weighted_value += mean * area
            crop_acc.area += area

    # Each member's clear passes: area-weighted health where the area-weighted clear fraction meets
    # the floor. Members with no analyses keep an empty series but still count toward the total.
    members: dict[str, list[MemberPass]] = {mid: [] for mid in member_ids}
    for (canonical_farm_id, pass_date), acc in overall.items():
        if acc.area <= 0:
            continue
        if acc.weighted_clear / acc.area < clear_floor:
            continue
        members[canonical_farm_id].append(
            MemberPass(pass_date=pass_date, value=acc.weighted_value / acc.area)
        )

    reference = select_group_reference_pass(members, quorum=quorum, window_days=window_days)
    if reference is None:
        return ClusterStats(
            region_boundary_id=region_boundary_id,
            index_name=index_name,
            members_total=len(member_ids),
            reference=None,
            members=[],
        )

    member_records = _rank_members(
        reference, per_crop=per_crop, index_name=index_name, min_cohort=min_cohort
    )
    return ClusterStats(
        region_boundary_id=region_boundary_id,
        index_name=index_name,
        members_total=len(member_ids),
        reference=reference,
        members=member_records,
    )


def _rank_members(
    reference: GroupReferencePass,
    *,
    per_crop: dict[tuple[str, date], dict[str, _CropAcc]],
    index_name: str,
    min_cohort: int,
) -> list[ClusterMemberStanding]:
    """Rank each contributing member within the group at the reference pass. The overall status uses
    the crop-agnostic classification (a mixed farm, matching the farm-analytics summary); each
    crop's standing uses the crop-aware one. Comparing statuses across crops stays fair because
    `classify` is crop-tuned; comparing raw NDVI across crops never happens."""
    from rs_interpret import classify, vigour_to_status

    # The crop-stratified values for every contributing member at its own contributing pass.
    member_crop_values: dict[str, dict[str, float]] = {}
    for contribution in reference.contributions:
        crops = per_crop.get((contribution.member_id, contribution.pass_date), {})
        member_crop_values[contribution.member_id] = {
            crop: acc.weighted_value / acc.area for crop, acc in crops.items() if acc.area > 0
        }

    cohort_crop_values: dict[str, list[float]] = defaultdict(list)
    for values in member_crop_values.values():
        for crop, value in values.items():
            cohort_crop_values[crop].append(value)

    group_statuses = [
        vigour_to_status(classify(index_name, contribution.value).label)
        for contribution in reference.contributions
    ]

    records: list[ClusterMemberStanding] = []
    for contribution in reference.contributions:
        crop_values = member_crop_values[contribution.member_id]
        crop_statuses = {
            crop: vigour_to_status(classify(index_name, value, crop).label)
            for crop, value in crop_values.items()
        }
        standings = compute_standing(
            target_crop_values=crop_values,
            cohort_crop_values=cohort_crop_values,
            target_crop_statuses=crop_statuses,
            group_statuses=group_statuses,
            min_cohort=min_cohort,
        )
        records.append(
            ClusterMemberStanding(
                member_id=contribution.member_id,
                pass_date=contribution.pass_date,
                day_offset=contribution.day_offset,
                value=contribution.value,
                status=vigour_to_status(classify(index_name, contribution.value).label),
                standings=standings,
            )
        )
    return records
