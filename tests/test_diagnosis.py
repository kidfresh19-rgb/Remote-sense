"""Pure tests for the Ward Watch diagnosis vocabulary + validation (backlog 0038): the controlled
fields normalise and the required ones reject unknown values loudly, so a label is never silently
stored as free text. No DB, no network."""

from __future__ import annotations

import pytest
from rs_core.diagnosis import (
    DiagnosisAction,
    DiagnosisCause,
    DiagnosisCondition,
    validate_diagnosis,
)


def test_valid_diagnosis_normalises_controlled_fields() -> None:
    fields = validate_diagnosis(
        observed_crop="Maize",
        condition="water_stress",
        cause="insufficient_rain",
        recommended_action="irrigate",
        notes="  leaves curling  ",
    )
    assert fields.observed_crop == "maize"
    assert fields.condition is DiagnosisCondition.WATER_STRESS
    assert fields.cause is DiagnosisCause.INSUFFICIENT_RAIN
    assert fields.recommended_action is DiagnosisAction.IRRIGATE
    assert fields.notes == "leaves curling"


def test_action_and_notes_are_optional() -> None:
    fields = validate_diagnosis(observed_crop="cowpea", condition="healthy", cause="unknown")
    assert fields.recommended_action is None
    assert fields.notes is None


def test_blank_notes_become_none() -> None:
    fields = validate_diagnosis(
        observed_crop="maize", condition="healthy", cause="unknown", notes="   "
    )
    assert fields.notes is None


def test_unknown_crop_is_rejected() -> None:
    with pytest.raises(ValueError, match="observed_crop"):
        validate_diagnosis(observed_crop="dragonfruit", condition="healthy", cause="unknown")


def test_blank_crop_is_rejected() -> None:
    with pytest.raises(ValueError, match="observed_crop is required"):
        validate_diagnosis(observed_crop="   ", condition="healthy", cause="unknown")


def test_unknown_condition_is_rejected() -> None:
    with pytest.raises(ValueError, match="condition"):
        validate_diagnosis(observed_crop="maize", condition="vibes", cause="unknown")


def test_unknown_cause_is_rejected() -> None:
    with pytest.raises(ValueError, match="cause"):
        validate_diagnosis(observed_crop="maize", condition="healthy", cause="aliens")


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(ValueError, match="recommended_action"):
        validate_diagnosis(
            observed_crop="maize", condition="healthy", cause="unknown", recommended_action="dance"
        )
