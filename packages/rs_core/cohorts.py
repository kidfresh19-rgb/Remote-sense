"""Pure cohort-level selection for Ward Watch (PRD 0003 §6.3): the small-cohort fallback ladder.
No DB, no network. Communal ward x crop x planting-window cohorts can be tiny, and scoring a
household against three peers is noise; the ladder widens the comparison until the cohort clears a
quorum, and records which level was actually used so a thin sample is always visible.

Cohort assembly and persistence live in the comparison-groups layer (ADR 0010) that this extends;
this module only chooses the rung."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class CohortLevel(StrEnum):
    """The fallback ladder, narrowest to widest (PRD 0003 §6.3)."""

    WARD_CROP_WINDOW = "ward_crop_window"
    WARD_CROP = "ward_crop"
    DISTRICT_CROP = "district_crop"
    NATURAL_REGION_CROP = "natural_region_crop"


# Narrowest -> widest; the order the ladder is climbed.
COHORT_LADDER: tuple[CohortLevel, ...] = (
    CohortLevel.WARD_CROP_WINDOW,
    CohortLevel.WARD_CROP,
    CohortLevel.DISTRICT_CROP,
    CohortLevel.NATURAL_REGION_CROP,
)
_LADDER_ORDER = {level: index for index, level in enumerate(COHORT_LADDER)}


@dataclass(frozen=True)
class CohortRung:
    """One candidate cohort level and how many members it would have."""

    level: CohortLevel
    size: int


@dataclass(frozen=True)
class CohortSelection:
    """The chosen level, its size, and whether it actually cleared the quorum. `meets_quorum` is
    False when no rung was big enough and the widest available was used as a best effort."""

    level: CohortLevel
    size: int
    meets_quorum: bool


def select_cohort_level(rungs: Sequence[CohortRung], *, n_min: int) -> CohortSelection:
    """Pick the narrowest cohort level whose size is at least `n_min`. If no rung clears the quorum,
    fall back to the widest available rung (the largest sample) and flag `meets_quorum=False` so the
    result can be shown as a thin sample. Rungs may be given in any order and need not cover every
    level; only the supplied rungs are considered.

    Raises ValueError if `n_min` is below 1 or `rungs` is empty."""
    if n_min < 1:
        raise ValueError(f"n_min must be at least 1, got {n_min}")
    if not rungs:
        raise ValueError("at least one cohort rung is required")

    ordered = sorted(rungs, key=lambda rung: _LADDER_ORDER[rung.level])
    for rung in ordered:
        if rung.size >= n_min:
            return CohortSelection(level=rung.level, size=rung.size, meets_quorum=True)
    widest = ordered[-1]
    return CohortSelection(level=widest.level, size=widest.size, meets_quorum=False)
