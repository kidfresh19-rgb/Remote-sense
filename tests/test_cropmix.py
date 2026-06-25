"""Pure tests for Ward Watch crop-mix validation and dominant-crop derivation (PRD 0003 §8.3,
backlog 0029). Zero DB, zero network - declared mixes in, a resolved mix out."""

from __future__ import annotations

import pytest
from rs_core.cropmix import CropWeight, resolve_crop_mix


def _mix(*pairs: tuple[str, float]) -> list[CropWeight]:
    return [CropWeight(crop=c, weight_pct=w) for c, w in pairs]


def test_full_intercrop_mix_persists_and_resolves_dominant() -> None:
    # AC1: a maize / cowpea / squash plot keeps the full mix and resolves dominant_crop = maize.
    resolved = resolve_crop_mix(_mix(("maize", 60), ("cowpea", 30), ("squash", 10)))
    assert resolved.dominant_crop == "maize"
    assert [e.crop for e in resolved.entries] == ["maize", "cowpea", "squash"]
    assert [e.weight_pct for e in resolved.entries] == [60, 30, 10]


def test_unknown_crop_is_rejected() -> None:
    # AC2: an out-of-vocabulary crop is a loud rejection, never silently stored.
    with pytest.raises(ValueError, match="unknown crop"):
        resolve_crop_mix(_mix(("maize", 70), ("unobtainium", 30)))


def test_weights_must_sum_to_about_100() -> None:
    with pytest.raises(ValueError, match="sum to ~100"):
        resolve_crop_mix(_mix(("maize", 60), ("cowpea", 60)))  # 120
    # Within the default tolerance is accepted (rough field estimate).
    ok = resolve_crop_mix(_mix(("maize", 70), ("cowpea", 30.5)), weight_sum_tolerance=1.0)
    assert ok.dominant_crop == "maize"


def test_dominant_tie_breaks_on_canonical_order() -> None:
    # Equal shares: the canonical crop order decides, so the result is deterministic. maize ranks
    # ahead of cowpea, so maize is dominant regardless of input order.
    resolved = resolve_crop_mix(_mix(("cowpea", 50), ("maize", 50)))
    assert resolved.dominant_crop == "maize"


def test_crop_names_are_normalised() -> None:
    resolved = resolve_crop_mix(_mix((" Maize ", 80), ("COWPEA", 20)))
    assert [e.crop for e in resolved.entries] == ["maize", "cowpea"]
    assert resolved.dominant_crop == "maize"


def test_duplicate_crop_is_rejected() -> None:
    with pytest.raises(ValueError, match="more than once"):
        resolve_crop_mix(_mix(("maize", 50), ("maize", 50)))


def test_non_positive_or_over_100_weight_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be in"):
        resolve_crop_mix(_mix(("maize", 0)))
    with pytest.raises(ValueError, match="must be in"):
        resolve_crop_mix(_mix(("maize", -10), ("cowpea", 110)))


def test_empty_mix_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one entry"):
        resolve_crop_mix([])


def test_single_crop_plot_is_its_own_dominant() -> None:
    resolved = resolve_crop_mix(_mix(("sorghum", 100)))
    assert resolved.dominant_crop == "sorghum"
    assert len(resolved.entries) == 1
