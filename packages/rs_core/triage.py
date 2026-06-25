"""Pure officer triage-queue ranking for Ward Watch (PRD 0003 §7.1, backlog 0036). The officer
cockpit is a capped, ranked weekly list - not a wall of 300 households - and it reads the movement
lens directly: it never invents a flat distress score (the 2x2 label is the decision). This module
turns a set of classified households into that ordered, capped queue.

Ranking is by movement-label severity first, then by robust deviation within a label: a household
failing on its own (idiosyncratic) is the highest-value officer visit, then a household sliding with
its whole cohort (systemic). Each row carries the cohort level the lens actually used and the
pixel-quality flag, because an honest queue shows when a ranking rests on a thin cohort or a
two-pixel plot rather than hiding it (PRD 0003 §4, §7.1).

No DB, no network. Classified households in (the descriptive join - village, distance, crop mix,
sparkline - is the API's job), a ranked queue out. Reuses `MovementLabel` (rs_core.movement) and
`CohortLevel` (rs_core.cohorts)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from rs_core.cohorts import CohortLevel
from rs_core.movement import MovementLabel

H = TypeVar("H")

# A sensible default cap for one officer's weekly list (PRD 0003 §7.1 "for example top 15").
DEFAULT_QUEUE_CAP = 15

# Triage severity, highest officer priority first. Idiosyncratic (one farm failing while peers hold)
# is the clearest call to visit; systemic (the whole cohort sliding) is a decline too but escalates
# up the food-security path (rollups, 0039); resilient and nominal are not distress.
_SEVERITY_ORDER: tuple[MovementLabel, ...] = (
    MovementLabel.IDIOSYNCRATIC,
    MovementLabel.SYSTEMIC,
    MovementLabel.RESILIENT,
    MovementLabel.NOMINAL,
)
_SEVERITY_RANK: dict[MovementLabel, int] = {
    label: index for index, label in enumerate(_SEVERITY_ORDER)
}


@dataclass(frozen=True)
class TriageCandidate(Generic[H]):
    """One classified household eligible for the queue: its movement label and robust deviation (the
    lens output, rs_core.movement), the cohort level the lens used and whether that cohort cleared
    quorum (rs_core.cohorts), and whether its stat rests on too few clear pixels to trust (§4)."""

    household_id: H
    label: MovementLabel
    robust_deviation: float
    cohort_level: CohortLevel
    cohort_meets_quorum: bool
    low_pixel_quality: bool


@dataclass(frozen=True)
class TriageRow(Generic[H]):
    """One placed row of the queue: its 1-based `rank` plus the candidate's fields, carried through
    so the cockpit shows the honesty flags (cohort level, quorum, pixel quality) on every row."""

    rank: int
    household_id: H
    label: MovementLabel
    robust_deviation: float
    cohort_level: CohortLevel
    cohort_meets_quorum: bool
    low_pixel_quality: bool


def build_triage_queue(
    candidates: Sequence[TriageCandidate[H]],
    *,
    cap: int = DEFAULT_QUEUE_CAP,
) -> list[TriageRow[H]]:
    """Rank `candidates` into the capped weekly queue.

    Ordering is by label severity (idiosyncratic, then systemic, then resilient, then nominal) and,
    within a label, by robust deviation descending (the worst relative to peers first). The sort is
    stable, so candidates that tie keep their input order. The top `cap` are returned with 1-based
    ranks. Raises ValueError if `cap` is below 1."""
    if cap < 1:
        raise ValueError(f"cap must be at least 1, got {cap}")

    ordered = sorted(
        candidates,
        key=lambda c: (_SEVERITY_RANK[c.label], -c.robust_deviation),
    )
    return [
        TriageRow(
            rank=rank,
            household_id=c.household_id,
            label=c.label,
            robust_deviation=c.robust_deviation,
            cohort_level=c.cohort_level,
            cohort_meets_quorum=c.cohort_meets_quorum,
            low_pixel_quality=c.low_pixel_quality,
        )
        for rank, c in enumerate(ordered[:cap], start=1)
    ]
