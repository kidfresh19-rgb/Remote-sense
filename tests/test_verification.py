"""Pure tests for Ward Watch phenology-shape crop verification (PRD 0003 §4, backlog 0035): does the
satellite signal agree with the declared crop, and is the plot fit to enter its cohort? Zero DB,
zero network - synthetic NDVI seasons only."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from rs_core.verification import (
    CropVerdict,
    template_for,
    verify_declared_crop,
    verify_for_crop,
)

_D0 = date(2024, 1, 1)


def _weekly(values: list[float]) -> tuple[list[date], list[float]]:
    dates = [_D0 + timedelta(days=7 * i) for i in range(len(values))]
    return dates, values


# A clean maize bell: bare baseline, dense peak past mid-season, senescence back down.
_MAIZE_BELL = [0.20, 0.40, 0.62, 0.82, 0.70, 0.45, 0.25]
# Bare soil all season under a maize declaration: never greens up.
_BARE = [0.12, 0.14, 0.11, 0.13, 0.12, 0.14, 0.13]


def test_maize_tracking_its_template_agrees() -> None:
    dates, ndvi = _weekly(_MAIZE_BELL)
    result = verify_for_crop(dates, ndvi, "maize")
    assert result.confidence >= 90.0
    assert result.verdict is CropVerdict.AGREE
    assert result.cohort_eligible
    assert result.reasons == ()


def test_declared_maize_reading_bare_soil_fails_verification() -> None:
    dates, ndvi = _weekly(_BARE)
    result = verify_for_crop(dates, ndvi, "maize")
    assert result.confidence < 60.0
    assert result.verdict is CropVerdict.VERIFY
    # The whole point: a contradicted plot is held out of its cohort, not compared against peers.
    assert not result.cohort_eligible
    assert any("below" in reason for reason in result.reasons)


def test_verify_plot_is_held_out_but_agree_plot_is_eligible() -> None:
    good = verify_for_crop(*_weekly(_MAIZE_BELL), "maize")
    bad = verify_for_crop(*_weekly(_BARE), "maize")
    assert good.cohort_eligible and good.weight == pytest.approx(good.confidence / 100.0)
    assert not bad.cohort_eligible


def test_hold_out_threshold_can_exclude_a_minor_plot() -> None:
    # A sparse-but-real maize stand lands in MINOR: eligible by default, excluded by a strict cut.
    dates, ndvi = _weekly([0.18, 0.30, 0.45, 0.55, 0.50, 0.35, 0.20])
    lenient = verify_for_crop(dates, ndvi, "maize")
    strict = verify_for_crop(dates, ndvi, "maize", hold_out_below=90.0)
    assert lenient.verdict is CropVerdict.MINOR
    assert lenient.cohort_eligible
    assert not strict.cohort_eligible


def test_declared_fallow_that_greens_up_fails() -> None:
    # The inverse gate: a fallow declaration with a full green-up season is a verify.
    dates, ndvi = _weekly(_MAIZE_BELL)
    result = verify_for_crop(dates, ndvi, "fallow")
    assert result.verdict is CropVerdict.VERIFY
    assert not result.cohort_eligible
    assert any("above" in reason for reason in result.reasons)


def test_genuinely_fallow_plot_agrees() -> None:
    dates, ndvi = _weekly(_BARE)
    result = verify_for_crop(dates, ndvi, "fallow")
    assert result.verdict is CropVerdict.AGREE
    assert result.cohort_eligible


def test_sorghum_lower_peak_still_agrees() -> None:
    # Sorghum peaks lower than maize; a ~0.5 peak that is a healthy drought-tolerant stand sits
    # below the maize floor, so its template should agree where maize only reaches MINOR.
    dates, ndvi = _weekly([0.15, 0.28, 0.42, 0.52, 0.45, 0.30, 0.18])
    sorghum = verify_for_crop(dates, ndvi, "sorghum")
    maize = verify_for_crop(dates, ndvi, "maize")
    assert sorghum.verdict is CropVerdict.AGREE
    assert maize.verdict is CropVerdict.MINOR
    assert maize.confidence < sorghum.confidence


def test_off_phase_peak_is_dampened_not_failed() -> None:
    # A healthy but still-rising season (peak at the final pass) keeps its level but loses on
    # timing, landing in MINOR rather than AGREE.
    dates, ndvi = _weekly([0.20, 0.35, 0.50, 0.68, 0.82])
    result = verify_for_crop(dates, ndvi, "maize")
    assert result.verdict is CropVerdict.MINOR
    assert any("season" in reason for reason in result.reasons)


def test_nan_passes_are_dropped() -> None:
    dates, ndvi = _weekly([0.20, float("nan"), 0.62, 0.82, float("nan"), 0.45, 0.25])
    result = verify_for_crop(dates, ndvi, "maize")
    assert result.peak_value == pytest.approx(0.82)
    assert result.verdict is CropVerdict.AGREE


def test_too_few_observations_raises() -> None:
    dates, ndvi = _weekly([0.3, 0.6])
    with pytest.raises(ValueError, match="at least 3"):
        verify_for_crop(dates, ndvi, "maize")


def test_unknown_crop_raises() -> None:
    with pytest.raises(KeyError, match="no crop-verification template"):
        template_for("rapeseed")


def test_out_of_range_hold_out_rejected() -> None:
    dates, ndvi = _weekly(_MAIZE_BELL)
    with pytest.raises(ValueError, match="hold_out_below"):
        verify_declared_crop(dates, ndvi, template=template_for("maize"), hold_out_below=150.0)
