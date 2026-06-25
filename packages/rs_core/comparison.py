"""Pure comparison-group reads: the group reference pass and a farm's standing within its cluster
(comparison engine, PRD 0002 slice 2a / backlog 0003). No DB, no network, and no interpretation
import. The area-weighted, crop-aware health *value* and *status* are computed once in the read path
(against the one farm-analytics definition) and passed in here as data, so this module stays
unit-testable on synthetic series exactly like `rs_core.geo` and `rs_core.regions`.

Two pieces:
- `select_group_reference_pass`: the most recent date by which a quorum of a cluster's members holds
  a clear read, each member contributing its nearest clear pass within +/- N days; members with no
  in-window clear pass sit out, and the contributing-versus-total clear fraction is surfaced.
- `crop_standing` / `compute_standing`: a farm's crop-stratified percentile within the group,
  ranked only against same-crop peers, never raw NDVI across crops - falling back to the crop-aware
  classified-status distribution where a crop is too sparse to stratify.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

# Defaults are configurable at the read-path boundary (rs_core.config); the pure functions carry
# them so a synthetic-series test reads naturally. Quorum is a member fraction; the window is days.
DEFAULT_QUORUM = 0.5
DEFAULT_WINDOW_DAYS = 14
# Fewer same-crop peers than this cannot give a meaningful percentile, so standing falls back to the
# crop-aware status distribution rather than ranking against one or two neighbours.
DEFAULT_MIN_COHORT = 4


@dataclass(frozen=True)
class MemberPass:
    """One clear pass for a cluster member: the date and the member's health value (the
    area-weighted crop-aware NDVI mean, already filtered to a usable per-AOI clear fraction)."""

    pass_date: date
    value: float


@dataclass(frozen=True)
class MemberContribution:
    """A member's reading at the group reference pass: the nearest clear pass it offered inside the
    window, with the signed day offset from the reference date (kept for honest day-gap labels)."""

    member_id: str
    pass_date: date
    value: float
    day_offset: int


@dataclass(frozen=True)
class GroupReferencePass:
    """The selected group reference pass: the reference date, every contributing member's reading,
    and the total membership. `contributing` / `total` is the clear fraction the read surfaces."""

    reference_date: date
    contributions: tuple[MemberContribution, ...]
    total: int

    @property
    def contributing(self) -> int:
        return len(self.contributions)

    @property
    def clear_fraction(self) -> float:
        return self.contributing / self.total if self.total else 0.0


def select_group_reference_pass(
    members: Mapping[str, Sequence[MemberPass]],
    *,
    quorum: float = DEFAULT_QUORUM,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> GroupReferencePass | None:
    """The most recent date on which at least `quorum` of `members` holds a clear read, each member
    aligned to its nearest clear pass within +/- `window_days` of that date. Members with no
    in-window pass sit out. `members` maps a member id to its clear passes (already filtered to a
    usable clear fraction); a member with an empty series still counts toward `total`, so a
    half-dark cluster never reads as fully reported. Returns None when no date reaches quorum.

    Deterministic: candidate dates (the union of every member's pass dates) are walked most-recent
    first and members in id order; an equidistant tie resolves to the earlier pass, matching the
    as-of policy (S3.1)."""
    total = len(members)
    if total == 0:
        return None
    candidate_dates = sorted(
        {p.pass_date for passes in members.values() for p in passes}, reverse=True
    )
    for ref in candidate_dates:
        contributions: list[MemberContribution] = []
        for member_id in sorted(members):
            in_window = [
                (abs((p.pass_date - ref).days), p.pass_date, p)
                for p in members[member_id]
                if abs((p.pass_date - ref).days) <= window_days
            ]
            if not in_window:
                continue
            _, _, best = min(in_window, key=lambda c: (c[0], c[1]))
            contributions.append(
                MemberContribution(
                    member_id=member_id,
                    pass_date=best.pass_date,
                    value=best.value,
                    day_offset=(best.pass_date - ref).days,
                )
            )
        if len(contributions) / total >= quorum:
            return GroupReferencePass(
                reference_date=ref, contributions=tuple(contributions), total=total
            )
    return None


@dataclass(frozen=True)
class CropStanding:
    """Where a farm stands for one crop within its group at the reference pass. `method`
    discriminates the two shapes: `percentile` (enough same-crop peers) sets `value` and
    `percentile`; `status_distribution` (the crop too sparse to rank on raw NDVI) sets `status` and
    `status_distribution` over the group's crop-aware classified statuses instead."""

    crop: str
    method: Literal["percentile", "status_distribution"]
    cohort_size: int
    value: float | None = None
    percentile: float | None = None
    status: str | None = None
    status_distribution: Mapping[str, float] | None = None


def _percentile(target: float, cohort: Sequence[float]) -> float:
    """The fraction of `cohort` at or below `target`, on a 0-100 scale. `cohort` includes the
    target, so the weakest member never scores 0 against itself and the strongest scores 100."""
    at_or_below = sum(1 for v in cohort if v <= target)
    return round(100.0 * at_or_below / len(cohort), 1)


def _status_distribution(statuses: Sequence[str]) -> dict[str, float]:
    """Each classified status' share of a population (0-1), status-name ordered for stability."""
    n = len(statuses)
    if n == 0:
        return {}
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return {status: round(count / n, 4) for status, count in sorted(counts.items())}


def crop_standing(
    *,
    crop: str,
    target_value: float,
    cohort_values: Sequence[float],
    target_status: str,
    group_statuses: Sequence[str],
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> CropStanding:
    """One crop's standing for a target farm. With at least `min_cohort` same-crop peers (the cohort
    includes the target), a raw-NDVI percentile within that crop; otherwise the crop-aware
    classified-status distribution across the whole group, because comparing statuses across crops
    is fair where comparing raw NDVI is not (`classify` is crop-tuned)."""
    cohort_size = len(cohort_values)
    if cohort_size >= min_cohort:
        return CropStanding(
            crop=crop,
            method="percentile",
            cohort_size=cohort_size,
            value=target_value,
            percentile=_percentile(target_value, cohort_values),
        )
    return CropStanding(
        crop=crop,
        method="status_distribution",
        cohort_size=cohort_size,
        status=target_status,
        status_distribution=_status_distribution(group_statuses),
    )


def compute_standing(
    *,
    target_crop_values: Mapping[str, float],
    cohort_crop_values: Mapping[str, Sequence[float]],
    target_crop_statuses: Mapping[str, str],
    group_statuses: Sequence[str],
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> list[CropStanding]:
    """A farm's standing for every crop it grows, crop-stratified. Each crop is ranked only against
    the same-crop population in `cohort_crop_values`; a sparse crop falls back to the group status
    distribution. Crops are returned in name order for a stable read."""
    return [
        crop_standing(
            crop=crop,
            target_value=target_crop_values[crop],
            cohort_values=cohort_crop_values.get(crop, (target_crop_values[crop],)),
            target_status=target_crop_statuses.get(crop, ""),
            group_statuses=group_statuses,
            min_cohort=min_cohort,
        )
        for crop in sorted(target_crop_values)
    ]
