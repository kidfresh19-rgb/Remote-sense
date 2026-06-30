"""Controlled vocabularies for the Ward Watch field-diagnosis flywheel (PRD 0003 §0, §12.9, backlog
0038). Every officer field diagnosis is a labelled training point: plot to observed crop to observed
condition to cause to recommended action. Controlled vocabularies (not free text) keep the labels
ML-usable for crop classification and yield calibration two or three seasons out; free-text notes
ride alongside for nuance.

Pure data + validation, zero DB. The observed crop reuses the canonical declared-crop vocabulary
(`rs_core.crops`); condition / cause / action are Ward-Watch-specific sets. ⚑ CONFIRM
(agronomy-scientist, PRD §12.9): the condition and cause vocabularies below are a literature/local
starting set; confirm and extend them before the diagnosis form ships to officers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rs_core.crops import is_declared_crop, normalize_crop


class DiagnosisCondition(StrEnum):
    """The crop condition an officer observes in the field (PRD 0003 §7.2/§12.9). ⚑ CONFIRM."""

    HEALTHY = "healthy"
    WATER_STRESS = "water_stress"
    WATERLOGGING = "waterlogging"
    PEST_DAMAGE = "pest_damage"
    DISEASE = "disease"
    NUTRIENT_DEFICIENCY = "nutrient_deficiency"
    WEED_PRESSURE = "weed_pressure"
    POOR_ESTABLISHMENT = "poor_establishment"
    CROP_FAILURE = "crop_failure"
    OTHER = "other"


class DiagnosisCause(StrEnum):
    """The underlying cause the officer attributes the condition to. ⚑ CONFIRM."""

    INSUFFICIENT_RAIN = "insufficient_rain"
    EXCESS_RAIN = "excess_rain"
    PEST = "pest"
    DISEASE = "disease"
    LOW_SOIL_FERTILITY = "low_soil_fertility"
    INPUT_ACCESS = "input_access"
    LATE_PLANTING = "late_planting"
    WEED_COMPETITION = "weed_competition"
    UNKNOWN = "unknown"
    OTHER = "other"


class DiagnosisAction(StrEnum):
    """The action the officer recommends (optional). ⚑ CONFIRM."""

    IRRIGATE = "irrigate"
    IMPROVE_DRAINAGE = "improve_drainage"
    APPLY_FERTILISER = "apply_fertiliser"
    PEST_CONTROL = "pest_control"
    DISEASE_CONTROL = "disease_control"
    WEED_CONTROL = "weed_control"
    REPLANT = "replant"
    MONITOR = "monitor"
    REFER_FOR_SUPPORT = "refer_for_support"
    OTHER = "other"


@dataclass(frozen=True)
class DiagnosisFields:
    """The validated, controlled-vocabulary core of one diagnosis. `recommended_action` and `notes`
    are optional; the crop, condition and cause are required and always valid here."""

    observed_crop: str
    condition: DiagnosisCondition
    cause: DiagnosisCause
    recommended_action: DiagnosisAction | None
    notes: str | None


def _coerce(enum_cls: type[StrEnum], value: str, field_name: str) -> StrEnum:
    """Coerce a wire string to a vocabulary member, normalising case/space; a loud ValueError
    naming the valid options on an unknown value (so an invalid label is rejected, not stored)."""
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        options = ", ".join(member.value for member in enum_cls)
        raise ValueError(f"unknown {field_name} '{value}'; expected one of: {options}") from None


def validate_diagnosis(
    *,
    observed_crop: str,
    condition: str,
    cause: str,
    recommended_action: str | None = None,
    notes: str | None = None,
) -> DiagnosisFields:
    """Validate and normalise the diagnosis fields, raising ValueError on any unknown controlled
    value or a blank required field. The observed crop must be a declared crop (`rs_core.crops`);
    condition and cause must be in their vocabularies; the action is optional but validated when
    given; notes is free text, blanked to None when empty."""
    crop = normalize_crop(observed_crop)
    if not crop:
        raise ValueError("observed_crop is required")
    if not is_declared_crop(crop):
        raise ValueError(f"unknown observed_crop '{observed_crop}'")
    resolved_condition = _coerce(DiagnosisCondition, condition, "condition")
    resolved_cause = _coerce(DiagnosisCause, cause, "cause")
    resolved_action = (
        _coerce(DiagnosisAction, recommended_action, "recommended_action")
        if recommended_action
        else None
    )
    cleaned_notes = notes.strip() if notes and notes.strip() else None
    assert isinstance(resolved_condition, DiagnosisCondition)
    assert isinstance(resolved_cause, DiagnosisCause)
    assert resolved_action is None or isinstance(resolved_action, DiagnosisAction)
    return DiagnosisFields(
        observed_crop=crop,
        condition=resolved_condition,
        cause=resolved_cause,
        recommended_action=resolved_action,
        notes=cleaned_notes,
    )
