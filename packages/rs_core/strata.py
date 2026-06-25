"""Pure cohort strata for Ward Watch (PRD 0003 §6.3, backlog 0032): the communal peer-cohort KEY and
the planting-window bucket it depends on. No DB, no network - the keying primitive the cohort engine
(ADR 0010 peer cohorts) groups households by, built ahead of its persistence the way the movement
lens and fallback ladder were.

The existing comparison-groups peer cohort is (crop, Natural Region, size bucket). Ward Watch adds
two communal strata: ward (nested in the Natural Region) and planting window. The planting-window
stratum is not optional: comparing a late-planted plot against early-planted peers manufactures
false distress, because the late plot is simply earlier in its own season. The cohort always keys on
the Natural Region ASSIGNMENT, never a gateway-supplied region string (invariant 6, ADR 0010)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

# The agricultural year anchors in October, so the Nov-Apr rainfed season is contiguous and ordered
# (Nov < Dec < Jan) instead of wrapping the calendar new year.
DEFAULT_AG_YEAR_START_MONTH = 10


class PlantingWindow(StrEnum):
    """When in the season a plot was planted (PRD 0003 §6.3). EARLY is dry-planting before the main
    rains; MAIN is the bulk planting on the first effective rains; LATE is replants and late
    starters."""

    EARLY = "early"
    MAIN = "main"
    LATE = "late"


# ⚑ CONFIRM (agronomy-scientist): Zimbabwe Nov-Apr rainfed planting windows as (window, month, day)
# starts within the agricultural year. Literature/local starting values; calibrate per Natural
# Region in Phase 5. EARLY runs from the year anchor, MAIN from mid-November, LATE from January.
DEFAULT_WINDOW_STARTS: tuple[tuple[PlantingWindow, int, int], ...] = (
    (PlantingWindow.EARLY, 10, 1),
    (PlantingWindow.MAIN, 11, 15),
    (PlantingWindow.LATE, 1, 1),
)


def _ag_ordinal(day: date, anchor: date) -> int:
    """Days from the agricultural-year anchor to `day`."""
    return (day - anchor).days


def _anchor_for(day: date, start_month: int) -> date:
    """The agricultural-year anchor (start_month/01) on or before `day`."""
    anchor_year = day.year if day.month >= start_month else day.year - 1
    return date(anchor_year, start_month, 1)


def bucket_planting_window(
    planting_date: date,
    *,
    window_starts: Sequence[tuple[PlantingWindow, int, int]] = DEFAULT_WINDOW_STARTS,
    start_month: int = DEFAULT_AG_YEAR_START_MONTH,
) -> PlantingWindow:
    """Bucket a declared planting date into its season window.

    Each `window_starts` entry is the (window, month, day) the window opens within the agricultural
    year that starts at `start_month`. The date is placed in the latest window whose start it has
    reached. Ordering is by agricultural-year position, so a January date sorts after a November one
    in the same season rather than wrapping. Raises ValueError if `window_starts` is empty."""
    if not window_starts:
        raise ValueError("window_starts must not be empty")

    anchor = _anchor_for(planting_date, start_month)
    target = _ag_ordinal(planting_date, anchor)

    best: tuple[int, PlantingWindow] | None = None
    earliest: tuple[int, PlantingWindow] | None = None
    for window, month, day in window_starts:
        start_year = anchor.year if month >= start_month else anchor.year + 1
        start_ord = _ag_ordinal(date(start_year, month, day), anchor)
        if earliest is None or start_ord < earliest[0]:
            earliest = (start_ord, window)
        if start_ord <= target and (best is None or start_ord > best[0]):
            best = (start_ord, window)
    # Before even the first window opens (e.g. a stray off-season date): fall back to the earliest.
    chosen = best if best is not None else earliest
    assert chosen is not None  # window_starts is non-empty, so earliest is always set
    return chosen[1]


@dataclass(frozen=True)
class CohortKey:
    """The Ward Watch peer-cohort key (PRD 0003 §6.3). `natural_region` is the NR assignment (ADR
    0010), never a gateway region string. `size_class` is the comparison-groups size bucket carried
    through. Two plots share a cohort iff every field matches, so the same crop in two planting
    windows lands in two cohorts."""

    dominant_crop: str
    natural_region: str
    ward: str
    planting_window: PlantingWindow
    size_class: str | None = None

    def key_id(self) -> str:
        """A stable, human-readable id for the cohort, for persistence and logging."""
        return "|".join(
            (
                self.dominant_crop,
                self.natural_region,
                self.ward,
                self.planting_window.value,
                self.size_class or "*",
            )
        )


def cohort_key(
    *,
    dominant_crop: str,
    natural_region: str,
    ward: str,
    planting_window: PlantingWindow | str,
    size_class: str | None = None,
) -> CohortKey:
    """Build a `CohortKey`, normalising the string strata (trimmed, lowercased) so trivial casing
    never splits a cohort. `natural_region` must be the NR assignment, not a gateway region label.
    Raises ValueError if any required stratum is blank or `planting_window` is not a known
    window."""
    crop = dominant_crop.strip().lower()
    region = natural_region.strip().lower()
    ward_norm = ward.strip().lower()
    if not crop or not region or not ward_norm:
        raise ValueError("dominant_crop, natural_region and ward are all required")
    window = PlantingWindow(planting_window)  # raises ValueError on an unknown window
    size = size_class.strip().lower() if size_class else None
    return CohortKey(
        dominant_crop=crop,
        natural_region=region,
        ward=ward_norm,
        planting_window=window,
        size_class=size,
    )
