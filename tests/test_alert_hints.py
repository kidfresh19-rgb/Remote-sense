"""Pure tests for the Ward Watch alert-hint engine (backlog 0037, PRD 0003 §7.2): each spectral
signature maps to its category hint, every result is framed as a hint (never a diagnosis), hints
that need an unavailable index do not fire, and the list is ordered strongest-first. No network."""

from __future__ import annotations

from rs_core.alert_hints import (
    HINT_FRAMING,
    RECOMMENDED_QUESTIONS,
    AlertCategory,
    HintInputs,
    assess_alert_hints,
    recommended_questions,
)
from rs_core.movement import MovementLabel

DECLINE = [0.6, 0.6, 0.5, 0.5]  # half-window delta -0.1
RISE = [0.30, 0.30, 0.45, 0.45]  # half-window delta +0.15
STABLE = [0.6, 0.6, 0.6, 0.6]  # half-window delta 0.0


def _categories(hints) -> set[AlertCategory]:
    return {h.category for h in hints}


def test_ndmi_then_ndvi_decline_surfaces_water_stress_hint() -> None:
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, ndmi=DECLINE))
    water = next(h for h in hints if h.category is AlertCategory.WATER_STRESS)
    assert "suspected" in water.headline.lower()
    assert water.framing == HINT_FRAMING  # labelled as a hint, not a diagnosis (AC)


def test_water_stress_does_not_fire_without_a_moisture_drop() -> None:
    # NDVI declines but NDMI holds: this is not the water-stress signature.
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, ndmi=STABLE))
    assert AlertCategory.WATER_STRESS not in _categories(hints)


def test_ndvi_drop_with_ndmi_spike_is_waterlogging() -> None:
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, ndmi=RISE))
    assert AlertCategory.WATERLOGGING in _categories(hints)
    assert AlertCategory.WATER_STRESS not in _categories(hints)


def test_waterlogging_fires_on_scl_water_flag_without_ndmi() -> None:
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, scl_water=True))
    assert AlertCategory.WATERLOGGING in _categories(hints)


def test_red_edge_drop_on_idiosyncratic_household_is_pest_disease() -> None:
    hints = assess_alert_hints(HintInputs(ndre=DECLINE, movement_label=MovementLabel.IDIOSYNCRATIC))
    assert AlertCategory.PEST_DISEASE in _categories(hints)


def test_red_edge_drop_without_idiosyncratic_label_is_not_pest_disease() -> None:
    hints = assess_alert_hints(HintInputs(ndre=DECLINE, movement_label=MovementLabel.NOMINAL))
    assert AlertCategory.PEST_DISEASE not in _categories(hints)


def test_chronically_low_red_edge_is_nutrient_deficiency() -> None:
    low_flat = [0.15, 0.15, 0.15, 0.15]  # below the 0.20 floor, not declining
    hints = assess_alert_hints(HintInputs(ndre=low_flat))
    assert AlertCategory.NUTRIENT_DEFICIENCY in _categories(hints)


def test_green_up_lag_is_delayed_planting() -> None:
    hints = assess_alert_hints(HintInputs(ndvi=STABLE, green_up_lagging=True))
    assert AlertCategory.DELAYED_PLANTING in _categories(hints)


def test_severe_deviation_is_possible_failure() -> None:
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, robust_deviation=4.0))
    assert AlertCategory.POSSIBLE_FAILURE in _categories(hints)


def test_systemic_decline_is_possible_failure() -> None:
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, movement_label=MovementLabel.SYSTEMIC))
    assert AlertCategory.POSSIBLE_FAILURE in _categories(hints)


def test_ndvi_only_stable_household_raises_no_hints() -> None:
    assert assess_alert_hints(HintInputs(ndvi=STABLE)) == []


def test_hints_are_ordered_strongest_first() -> None:
    # Water stress (strength |ndmi|+|ndvi| = 0.2) ranks above a milder possible-failure (dev 3.0
    # vs. ... ) - assert the list is sorted by strength descending whatever fires.
    hints = assess_alert_hints(HintInputs(ndvi=DECLINE, ndmi=DECLINE, robust_deviation=3.0))
    strengths = [h.strength for h in hints]
    assert strengths == sorted(strengths, reverse=True)
    assert len(hints) >= 2  # both water stress and possible failure fired


def test_recommended_questions_follow_category_order_and_dedupe() -> None:
    questions = recommended_questions([AlertCategory.POSSIBLE_FAILURE, AlertCategory.WATER_STRESS])
    # The strongest hint's questions lead.
    assert questions[: len(RECOMMENDED_QUESTIONS[AlertCategory.POSSIBLE_FAILURE])] == list(
        RECOMMENDED_QUESTIONS[AlertCategory.POSSIBLE_FAILURE]
    )
    # Every water-stress question is present too, and nothing is duplicated.
    assert set(RECOMMENDED_QUESTIONS[AlertCategory.WATER_STRESS]) <= set(questions)
    assert len(questions) == len(set(questions))


def test_recommended_questions_empty_for_no_categories() -> None:
    assert recommended_questions([]) == []
