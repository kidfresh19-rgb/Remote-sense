"""Pure alert-hint engine for Ward Watch (PRD 0003 §7.2, backlog 0037). The cockpit flags an anomaly
and offers a most-likely CATEGORY HINT to prioritise an officer's attention; it never diagnoses. The
officer's field diagnosis is the ground truth (the §0 flywheel, captured by 0038), so every item it
returns carries the hint framing and the UI cannot present it as a verdict.

Each hint maps a spectral signature to a category, grounded in the existing indices (PRD §7.2):

    Water stress         NDMI decline + NDVI decline (moisture drop leading vegetation drop)
    Delayed planting     green-up onset lagging the cohort
    Pest / disease        NDRE drop ahead of canopy, on an idiosyncratic household
    Waterlogging          NDVI drop + NDMI spike (post-rain), or an SCL standing-water flag
    Nutrient deficiency   NDRE chronically low
    Possible failure      severe sustained negative deviation / a severe movement label

No DB, no network. It reads per-index series (oldest first) plus the household's movement label, so
it is unit-testable on synthetic inputs and emits only the hints its indices can support: with the
NDVI-only series ingested today (0031) the moisture and red-edge hints simply do not fire, which
is the honest behaviour until the index set widens. Thresholds are ⚑ CONFIRM (agronomy-scientist),
Phase 5 calibration per Natural Region."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from rs_core.movement import MovementLabel

# Every item is a hint, never a diagnosis (PRD 0003 §7.2). The framing travels on each hint so a
# consumer cannot strip it.
HINT_FRAMING = "Prioritisation hint, not a diagnosis. The officer's field diagnosis is the truth."

# ⚑ CONFIRM (agronomy-scientist, PRD §12 Phase 5): literature starting thresholds, calibrated per
# Natural Region later. `decline` is the index drop (half-window median delta) that counts as a
# decline; `spike` the NDMI rise that reads as a post-rain moisture spike; `ndre_low_floor` the
# red-edge level below which the canopy reads chronically low; `severe_deviation` the robust
# deviation below the cohort centre that reads as a severe sustained shortfall.
DEFAULT_DECLINE_THRESHOLD = 0.05
DEFAULT_SPIKE_THRESHOLD = 0.05
DEFAULT_NDRE_LOW_FLOOR = 0.20
DEFAULT_SEVERE_DEVIATION = 3.0


class AlertCategory(StrEnum):
    """The most-likely category an anomaly hint points at (PRD 0003 §7.2)."""

    WATER_STRESS = "water_stress"
    DELAYED_PLANTING = "delayed_planting"
    PEST_DISEASE = "pest_disease"
    WATERLOGGING = "waterlogging"
    NUTRIENT_DEFICIENCY = "nutrient_deficiency"
    POSSIBLE_FAILURE = "possible_failure"


@dataclass(frozen=True)
class AlertHint:
    """One prioritisation hint: its category, a short headline, the plain-language spectral
    signature behind it, and a `strength` for ordering (higher = stronger). `framing` is the
    standing reminder that this is a hint, not a diagnosis (PRD §7.2)."""

    category: AlertCategory
    headline: str
    signature: str
    strength: float
    framing: str = HINT_FRAMING


@dataclass(frozen=True)
class HintInputs:
    """The signals the engine reads for one household. Each index is its series over the baseline
    window (oldest first); a missing index is None and the hints that need it simply do not fire.
    `movement_label` and `robust_deviation` come from the cohort movement lens (rs_core.movement);
    `scl_water` flags standing-water pixels; `green_up_lagging` is the phenology-vs-cohort signal
    for delayed planting (None when not yet computed)."""

    ndvi: Sequence[float] | None = None
    ndmi: Sequence[float] | None = None
    ndre: Sequence[float] | None = None
    movement_label: MovementLabel | None = None
    robust_deviation: float | None = None
    scl_water: bool = False
    green_up_lagging: bool | None = None


def _delta(series: Sequence[float] | None) -> float | None:
    """Robust half-window change (recent-half median minus earlier-half median), the same shape the
    movement lens uses; None when there are too few points to read a trend."""
    if series is None or len(series) < 2:
        return None
    half = len(series) // 2
    return median(series[half:]) - median(series[:half])


def _level(series: Sequence[float] | None) -> float | None:
    """The recent level (median of the recent half); None for an empty series."""
    if not series:
        return None
    half = len(series) // 2
    return median(series[half:] if len(series) >= 2 else series)


def assess_alert_hints(
    inputs: HintInputs,
    *,
    decline_threshold: float = DEFAULT_DECLINE_THRESHOLD,
    spike_threshold: float = DEFAULT_SPIKE_THRESHOLD,
    ndre_low_floor: float = DEFAULT_NDRE_LOW_FLOOR,
    severe_deviation: float = DEFAULT_SEVERE_DEVIATION,
) -> list[AlertHint]:
    """Map a household's spectral signals to ordered category hints (PRD §7.2). Returns the hints
    whose signatures fire, strongest first; an empty list when nothing is anomalous (or no index
    supports a hint). Every returned item is a prioritisation hint, never a diagnosis."""
    ndvi_d = _delta(inputs.ndvi)
    ndmi_d = _delta(inputs.ndmi)
    ndre_d = _delta(inputs.ndre)
    ndre_level = _level(inputs.ndre)

    ndvi_declining = ndvi_d is not None and ndvi_d <= -decline_threshold
    ndmi_declining = ndmi_d is not None and ndmi_d <= -decline_threshold
    ndmi_spiking = ndmi_d is not None and ndmi_d >= spike_threshold
    ndre_declining = ndre_d is not None and ndre_d <= -decline_threshold

    hints: list[AlertHint] = []

    # Water stress: moisture drop leading vegetation drop (NDMI decline + NDVI decline).
    if ndmi_declining and ndvi_declining:
        assert ndmi_d is not None and ndvi_d is not None
        hints.append(
            AlertHint(
                category=AlertCategory.WATER_STRESS,
                headline="Water stress (suspected)",
                signature="Moisture (NDMI) and canopy (NDVI) both declining, moisture leading.",
                strength=abs(ndmi_d) + abs(ndvi_d),
            )
        )

    # Waterlogging: a canopy drop with a post-rain moisture spike, or standing-water pixels.
    if ndvi_declining and (ndmi_spiking or inputs.scl_water):
        assert ndvi_d is not None
        spike = abs(ndmi_d) if (ndmi_spiking and ndmi_d is not None) else 0.0
        hints.append(
            AlertHint(
                category=AlertCategory.WATERLOGGING,
                headline="Flooding / waterlogging (suspected)",
                signature="Canopy (NDVI) dropping with an NDMI moisture spike or standing water.",
                strength=abs(ndvi_d) + spike,
            )
        )

    # Pest / disease: red-edge falling ahead of the canopy on a household failing alone.
    if ndre_declining and inputs.movement_label is MovementLabel.IDIOSYNCRATIC:
        assert ndre_d is not None
        hints.append(
            AlertHint(
                category=AlertCategory.PEST_DISEASE,
                headline="Pest / disease (suspected)",
                signature="Red-edge (NDRE) falling ahead of the canopy; household failing alone.",
                strength=abs(ndre_d),
            )
        )

    # Nutrient deficiency: chronically low red-edge that is not a sharp recent drop (persistent).
    if ndre_level is not None and ndre_level < ndre_low_floor and not ndre_declining:
        hints.append(
            AlertHint(
                category=AlertCategory.NUTRIENT_DEFICIENCY,
                headline="Nutrient deficiency (suspected)",
                signature="Red-edge (NDRE) persistently low across the window.",
                strength=ndre_low_floor - ndre_level,
            )
        )

    # Delayed planting: green-up onset lagging the cohort (phenology signal supplied upstream).
    if inputs.green_up_lagging:
        hints.append(
            AlertHint(
                category=AlertCategory.DELAYED_PLANTING,
                headline="Delayed planting (suspected)",
                signature="Green-up onset lagging the cohort's phenology.",
                strength=1.0,
            )
        )

    # Possible failure: a severe sustained shortfall, or the whole cohort sliding with this plot.
    severe = inputs.robust_deviation is not None and inputs.robust_deviation >= severe_deviation
    if severe or (inputs.movement_label is MovementLabel.SYSTEMIC and ndvi_declining):
        strength = (
            inputs.robust_deviation if inputs.robust_deviation is not None else severe_deviation
        )
        hints.append(
            AlertHint(
                category=AlertCategory.POSSIBLE_FAILURE,
                headline="Possible crop failure",
                signature="Severe sustained negative deviation from the cohort.",
                strength=strength,
            )
        )

    hints.sort(key=lambda hint: -hint.strength)
    return hints


# ⚑ CONFIRM (agronomy-scientist): the officer's field-visit prompts per hint category (PRD 0003
# §7.3). These guide the visit and feed the diagnosis flywheel (0038); the officer's answer is the
# ground truth, the hint is only a prompt. Starting set, refine with the extension team.
RECOMMENDED_QUESTIONS: dict[AlertCategory, tuple[str, ...]] = {
    AlertCategory.WATER_STRESS: (
        "Is there visible wilting or leaf curling?",
        "When did the plot last receive rain or irrigation?",
        "Are nearby water sources running dry?",
    ),
    AlertCategory.WATERLOGGING: (
        "Is there standing water or waterlogged soil on the plot?",
        "Did unusually heavy rain fall recently?",
        "Is the plot low-lying or poorly drained?",
    ),
    AlertCategory.PEST_DISEASE: (
        "Are pests, lesions, or leaf discolouration visible?",
        "Which part of the plant is affected (leaves, stem, roots)?",
        "Are neighbouring plots showing the same symptoms?",
    ),
    AlertCategory.NUTRIENT_DEFICIENCY: (
        "Is leaf yellowing uniform or patchy across the plot?",
        "Was fertiliser applied, and when?",
        "Has this plot been cropped continuously without rotation?",
    ),
    AlertCategory.DELAYED_PLANTING: (
        "When was the plot actually planted?",
        "Was planting delayed by seed, input, or labour access?",
        "Did the household wait for later rains?",
    ),
    AlertCategory.POSSIBLE_FAILURE: (
        "What share of the plot is affected?",
        "Is replanting or a replacement crop feasible this season?",
        "Does the household need food-security support?",
    ),
}


def recommended_questions(categories: Sequence[AlertCategory]) -> list[str]:
    """The officer's visit prompts for a set of hint categories, de-duplicated and kept in the
    categories' order (so the strongest hint's questions lead). An unknown category contributes
    nothing. These are prompts to guide the visit, never a script that presumes the diagnosis."""
    seen: set[str] = set()
    questions: list[str] = []
    for category in categories:
        for question in RECOMMENDED_QUESTIONS.get(category, ()):
            if question not in seen:
                seen.add(question)
                questions.append(question)
    return questions
