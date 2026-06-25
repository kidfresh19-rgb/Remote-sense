"""Pure food-security rollups for Ward Watch (PRD 0003 §10, backlog 0039). Household movement labels
aggregate cleanly upward: counting them per ward, then district, then province is the food-security
view. Systemic counts (whole cohorts sliding together) are the escalation signal; idiosyncratic
counts measure officer workload and local need. The two are always reported separately.

This reads the same 2x2 labels the officer queue does (rs_core.movement), so there is no parallel
statistics engine - one lens, aggregated at every level (PRD 0003 §10). No DB, no network: labelled
households in, per-level tallies out. The persistence and the API that serves the dashboards (0040)
wrap this."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rs_core.movement import MovementLabel


@dataclass(frozen=True)
class LabelCounts:
    """A tally of the 2x2 labels over some set of households. `distressed` is the declining pair
    (idiosyncratic + systemic); `systemic_fraction` is the share of the whole that is systemic, the
    food-security escalation indicator (0.0 when there are no households)."""

    total: int
    nominal: int
    resilient: int
    idiosyncratic: int
    systemic: int

    @property
    def distressed(self) -> int:
        return self.idiosyncratic + self.systemic

    @property
    def systemic_fraction(self) -> float:
        return self.systemic / self.total if self.total else 0.0

    @classmethod
    def from_labels(cls, labels: Iterable[MovementLabel]) -> LabelCounts:
        counts = {label: 0 for label in MovementLabel}
        total = 0
        for label in labels:
            counts[label] += 1
            total += 1
        return cls(
            total=total,
            nominal=counts[MovementLabel.NOMINAL],
            resilient=counts[MovementLabel.RESILIENT],
            idiosyncratic=counts[MovementLabel.IDIOSYNCRATIC],
            systemic=counts[MovementLabel.SYSTEMIC],
        )


@dataclass(frozen=True)
class RollupNode:
    """One administrative unit (a ward, district, or province) with its label tally."""

    name: str
    counts: LabelCounts


@dataclass(frozen=True)
class HouseholdLabel:
    """One household's place in the administrative hierarchy and its current movement label."""

    ward: str
    district: str
    province: str
    label: MovementLabel


@dataclass(frozen=True)
class FoodSecurityRollup:
    """The same households tallied at each level. Each list is ordered worst-first (most systemic,
    then most idiosyncratic, then by name) so the escalation surfaces at the top."""

    by_ward: list[RollupNode]
    by_district: list[RollupNode]
    by_province: list[RollupNode]


def _group(households: Sequence[HouseholdLabel], key: str) -> list[RollupNode]:
    grouped: dict[str, list[MovementLabel]] = {}
    for household in households:
        grouped.setdefault(getattr(household, key), []).append(household.label)
    nodes = [
        RollupNode(name=name, counts=LabelCounts.from_labels(labels))
        for name, labels in grouped.items()
    ]
    # Worst-first: most systemic, then most idiosyncratic, then name for a stable tie-break.
    nodes.sort(key=lambda n: (-n.counts.systemic, -n.counts.idiosyncratic, n.name))
    return nodes


def roll_up_food_security(households: Sequence[HouseholdLabel]) -> FoodSecurityRollup:
    """Tally household movement labels at ward, district, and province level. Idiosyncratic and
    systemic counts are reported separately at every level; a whole cohort going systemic in one
    ward therefore shows in that ward's count and again in its district's and province's totals."""
    return FoodSecurityRollup(
        by_ward=_group(households, "ward"),
        by_district=_group(households, "district"),
        by_province=_group(households, "province"),
    )
