"""Pure tests for Ward Watch food-security rollups (PRD 0003 §10, backlog 0039): movement labels
aggregate to separate idiosyncratic and systemic counts at ward, district, and province. Zero DB,
zero network."""

from __future__ import annotations

from rs_core.movement import MovementLabel
from rs_core.rollups import (
    HouseholdLabel,
    LabelCounts,
    RollupNode,
    roll_up_food_security,
)


def _node(nodes: list[RollupNode], name: str) -> RollupNode:
    return next(n for n in nodes if n.name == name)


def _households() -> list[HouseholdLabel]:
    # Goromonzi/Ward 3: a whole cohort gone systemic. Marondera/Ward 7: one idiosyncratic failure
    # amid stable peers. Both in Mashonaland East.
    systemic_ward = [
        HouseholdLabel("Ward 3", "Goromonzi", "Mash East", MovementLabel.SYSTEMIC) for _ in range(5)
    ]
    other_ward = [
        HouseholdLabel("Ward 7", "Marondera", "Mash East", MovementLabel.IDIOSYNCRATIC),
        HouseholdLabel("Ward 7", "Marondera", "Mash East", MovementLabel.NOMINAL),
        HouseholdLabel("Ward 7", "Marondera", "Mash East", MovementLabel.NOMINAL),
    ]
    return systemic_ward + other_ward


def test_systemic_cohort_shows_at_every_level() -> None:
    rollup = roll_up_food_security(_households())
    assert _node(rollup.by_ward, "Ward 3").counts.systemic == 5
    assert _node(rollup.by_district, "Goromonzi").counts.systemic == 5
    assert _node(rollup.by_province, "Mash East").counts.systemic == 5


def test_idiosyncratic_and_systemic_reported_separately_at_every_level() -> None:
    rollup = roll_up_food_security(_households())
    province = _node(rollup.by_province, "Mash East").counts
    assert province.systemic == 5
    assert province.idiosyncratic == 1
    assert province.nominal == 2
    assert province.total == 8
    assert province.distressed == 6


def test_nodes_are_ordered_worst_first() -> None:
    rollup = roll_up_food_security(_households())
    # The systemic ward leads the ward list; the food-security escalation surfaces at the top.
    assert rollup.by_ward[0].name == "Ward 3"


def test_systemic_fraction() -> None:
    rollup = roll_up_food_security(_households())
    province = _node(rollup.by_province, "Mash East").counts
    assert province.systemic_fraction == 5 / 8


def test_label_counts_from_labels() -> None:
    counts = LabelCounts.from_labels(
        [
            MovementLabel.NOMINAL,
            MovementLabel.SYSTEMIC,
            MovementLabel.SYSTEMIC,
            MovementLabel.RESILIENT,
        ]
    )
    assert counts.total == 4
    assert counts.systemic == 2
    assert counts.nominal == 1
    assert counts.resilient == 1
    assert counts.idiosyncratic == 0
    assert counts.distressed == 2


def test_empty_rollup_has_no_nodes() -> None:
    rollup = roll_up_food_security([])
    assert rollup.by_ward == []
    assert rollup.by_district == []
    assert rollup.by_province == []


def test_empty_label_counts_have_zero_systemic_fraction() -> None:
    counts = LabelCounts.from_labels([])
    assert counts.total == 0
    assert counts.systemic_fraction == 0.0
