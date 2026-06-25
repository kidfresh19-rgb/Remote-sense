"""Pure phenology-shape crop verification for Ward Watch (PRD 0003 §4, backlog 0035): does the
satellite signal agree with the crop the farmer declared? This runs BEFORE cohort assignment, so a
plot whose trajectory contradicts its declared crop can be down-weighted or held out of its cohort
instead of poisoning the peer comparison.

The declared crop is the source of truth; this never classifies a crop from spectra (no labels
exist yet). It only scores observed-against-expected for the declared crop, two ways:

  1. An absolute-index gate. A declared maize plot sitting at bare-soil NDVI through peak season, or
     a declared-fallow plot that greens up, fails on level alone, regardless of shape.
  2. A phenology-shape template fit. The plot's NDVI/EVI2 trajectory is reduced to a few robust
     season features (peak height, green-up amplitude, when in the season the peak falls) and each
     is scored against the declared crop's expected envelope.

The two combine into a 0..100 confidence score and a verdict: AGREE (>= 90), MINOR (60..89, watch),
VERIFY (< 90 fail / < hold-out, send the officer to look). Below the hold-out cut the plot is kept
out of its cohort. Cohort assembly and persistence live in the comparison-groups layer (ADR 0010)
that this extends; this module is the gate in front of it.

Pure: parallel `dates` + `ndvi` sequences in, a `CropVerification` out. No DB, no network, no
raster. The expected per-crop envelopes (`CROP_TEMPLATES`) are agronomy-owned and ship here as a
small literature-derived v1 set behind a CONFIRM marker; calibrated per-AEZ templates come in Phase
5 via the diagnosis flywheel."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

# Verdict cut points on the 0..100 confidence scale (PRD 0003 §4).
_AGREE_AT = 90.0
_VERIFY_BELOW = 60.0

# How the three season features combine into the confidence. Peak height and green-up amplitude are
# the absolute-index gate and carry most of the weight; peak timing is the softer shape nudge, so a
# still-rising or slightly off-phase season is dampened, not failed, on timing alone.
_W_PEAK = 0.45
_W_AMPLITUDE = 0.35
_W_TIMING = 0.20


class CropVerdict(StrEnum):
    """How far the satellite signal is from the declared crop (PRD 0003 §4)."""

    AGREE = "agree"
    MINOR = "minor"
    VERIFY = "verify"


@dataclass(frozen=True)
class Envelope:
    """An acceptable range for a single phenology feature. A value inside ``[lo, hi]`` scores 1.0;
    outside, the score falls off linearly to 0 over ``lo_falloff`` below ``lo`` or ``hi_falloff``
    above ``hi``. A ``None`` bound is open (no penalty on that side), so a one-sided floor (declared
    crop must green up) and a one-sided ceiling (declared fallow must not) use the same shape."""

    lo: float | None
    hi: float | None
    lo_falloff: float = 1.0
    hi_falloff: float = 1.0

    def score(self, value: float) -> float:
        if self.lo is not None and value < self.lo:
            return _clamp01(1.0 - (self.lo - value) / self.lo_falloff)
        if self.hi is not None and value > self.hi:
            return _clamp01(1.0 - (value - self.hi) / self.hi_falloff)
        return 1.0


@dataclass(frozen=True)
class CropTemplate:
    """The expected season shape for a declared crop. ``peak`` and ``amplitude`` are the absolute
    and green-up envelopes; ``peak_fraction`` is where in the observed season the peak should fall
    (0 at the first pass, 1 at the last) and ``peak_fraction_tol`` the half-width that still scores
    full. Agronomy-owned; the shipped set is literature-derived v1 (see ``CROP_TEMPLATES``)."""

    crop: str
    peak: Envelope
    amplitude: Envelope
    peak_fraction: float
    peak_fraction_tol: float


@dataclass(frozen=True)
class CropVerification:
    """The verdict for one plot against its declared crop. ``confidence`` is 0..100; ``weight`` is
    ``confidence / 100`` for down-weighting an included plot; ``cohort_eligible`` is False when the
    plot is held out of its cohort. The season features and the human-readable ``reasons`` (only the
    components that scored poorly) are carried for the officer."""

    crop: str
    confidence: float
    verdict: CropVerdict
    cohort_eligible: bool
    weight: float
    peak_value: float
    amplitude: float
    peak_fraction: float
    reasons: tuple[str, ...]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _timing_score(observed: float, expected: float, tol: float) -> float:
    """How close the observed peak position is to the expected one. Within ``tol`` scores 1.0, then
    falls off linearly to 0 one further ``tol`` away. A non-positive ``tol`` makes timing neutral
    (always 1.0), which is how fallow - with no meaningful peak - opts out of the timing term."""
    if tol <= 0.0:
        return 1.0
    distance = abs(observed - expected)
    if distance <= tol:
        return 1.0
    return _clamp01(1.0 - (distance - tol) / tol)


@dataclass(frozen=True)
class _SeasonFeatures:
    peak_value: float
    baseline: float
    amplitude: float
    peak_fraction: float


def _season_features(dates: Sequence[date], ndvi: Sequence[float]) -> _SeasonFeatures:
    """Reduce a plot's NDVI series to the robust season features the template scores against. NaN /
    None passes are dropped and the rest sorted by date. ``peak_fraction`` is the peak's position in
    calendar time (not index), so irregular revisit spacing is handled honestly. Needs at least 3
    valid observations."""
    pairs = sorted(
        (d, float(v))
        for d, v in zip(dates, ndvi, strict=True)
        if v is not None and not math.isnan(float(v))
    )
    if len(pairs) < 3:
        raise ValueError("crop verification needs at least 3 valid NDVI observations")
    ds = [d for d, _ in pairs]
    vs = [v for _, v in pairs]

    peak_idx = max(range(len(vs)), key=lambda i: vs[i])
    peak_value = vs[peak_idx]
    baseline = min(vs)
    span_days = (ds[-1] - ds[0]).days
    # A zero span (all passes on one day) leaves the peak position undefined; treat it as mid-season
    # so timing stays neutral rather than spuriously early or late.
    peak_fraction = (ds[peak_idx] - ds[0]).days / span_days if span_days > 0 else 0.5
    return _SeasonFeatures(
        peak_value=peak_value,
        baseline=baseline,
        amplitude=peak_value - baseline,
        peak_fraction=peak_fraction,
    )


def _reasons(template: CropTemplate, features: _SeasonFeatures, timing: float) -> tuple[str, ...]:
    """Short why-strings for the components that scored poorly, for the officer's triage card."""
    out: list[str] = []
    if template.peak.score(features.peak_value) < 0.9:
        if template.peak.lo is not None and features.peak_value < template.peak.lo:
            out.append(
                f"peak NDVI {features.peak_value:.2f} below the {template.crop} floor "
                f"{template.peak.lo:.2f}"
            )
        elif template.peak.hi is not None and features.peak_value > template.peak.hi:
            out.append(
                f"peak NDVI {features.peak_value:.2f} above the {template.crop} ceiling "
                f"{template.peak.hi:.2f}"
            )
    if template.amplitude.score(features.amplitude) < 0.9:
        if template.amplitude.lo is not None and features.amplitude < template.amplitude.lo:
            out.append(
                f"green-up amplitude {features.amplitude:.2f} below the expected "
                f"{template.amplitude.lo:.2f}"
            )
        elif template.amplitude.hi is not None and features.amplitude > template.amplitude.hi:
            out.append(
                f"green-up amplitude {features.amplitude:.2f} above the expected "
                f"{template.amplitude.hi:.2f}"
            )
    if timing < 0.9:
        out.append(
            f"peaked at {features.peak_fraction:.0%} of season, off the expected "
            f"{template.peak_fraction:.0%}"
        )
    return tuple(out)


def verify_declared_crop(
    dates: Sequence[date],
    ndvi: Sequence[float],
    *,
    template: CropTemplate,
    hold_out_below: float = _VERIFY_BELOW,
) -> CropVerification:
    """Score a plot's NDVI season against its declared crop's template and decide cohort
    eligibility.

    ``dates`` and ``ndvi`` are parallel sequences (one usable pass each, any order). The confidence
    is a weighted blend of the peak-height, green-up-amplitude and peak-timing envelope scores,
    scaled to 0..100. The verdict is AGREE (>= 90), MINOR (>= 60), else VERIFY. A plot scoring below
    ``hold_out_below`` (default 60) is held out of its cohort (``cohort_eligible=False``); the rest
    carry ``weight = confidence / 100`` for down-weighting.

    Raises ValueError if fewer than 3 valid observations are given or ``hold_out_below`` is outside
    0..100."""
    if not 0.0 <= hold_out_below <= 100.0:
        raise ValueError(f"hold_out_below must be within 0..100, got {hold_out_below}")

    features = _season_features(dates, ndvi)
    peak_score = template.peak.score(features.peak_value)
    amplitude_score = template.amplitude.score(features.amplitude)
    timing_score = _timing_score(
        features.peak_fraction, template.peak_fraction, template.peak_fraction_tol
    )
    confidence = 100.0 * (
        _W_PEAK * peak_score + _W_AMPLITUDE * amplitude_score + _W_TIMING * timing_score
    )

    if confidence >= _AGREE_AT:
        verdict = CropVerdict.AGREE
    elif confidence >= _VERIFY_BELOW:
        verdict = CropVerdict.MINOR
    else:
        verdict = CropVerdict.VERIFY

    return CropVerification(
        crop=template.crop,
        confidence=confidence,
        verdict=verdict,
        cohort_eligible=confidence >= hold_out_below,
        weight=confidence / 100.0,
        peak_value=features.peak_value,
        amplitude=features.amplitude,
        peak_fraction=features.peak_fraction,
        reasons=_reasons(template, features, timing_score),
    )


def verify_for_crop(
    dates: Sequence[date],
    ndvi: Sequence[float],
    crop: str,
    *,
    hold_out_below: float = _VERIFY_BELOW,
) -> CropVerification:
    """Convenience over `verify_declared_crop` that resolves the template from `CROP_TEMPLATES`.
    Raises KeyError for a crop with no shipped template."""
    return verify_declared_crop(
        dates, ndvi, template=template_for(crop), hold_out_below=hold_out_below
    )


def template_for(crop: str) -> CropTemplate:
    """The phenology template for a declared crop (case-insensitive). Raises KeyError if none is
    shipped, so an unknown declaration is a loud miss rather than a silent pass."""
    try:
        return CROP_TEMPLATES[crop.lower()]
    except KeyError as exc:
        raise KeyError(f"no crop-verification template for {crop!r}") from exc


# --- Shipped v1 templates ----------------------------------------------------------------------
# ⚑ CONFIRM (agronomy-scientist): literature-derived starting envelopes, not yet agronomist-signed
# off. NDVI peak ranges and green-up amplitudes follow the per-crop notes in
# rs_interpret/thresholds.py and the Zimbabwe Nov-Apr rainfed season (peak fraction is the peak's
# position across the OBSERVED window, so it assumes the window roughly spans the season). Phase 5
# replaces these with per-AEZ templates calibrated from the diagnosis flywheel.

# Maize: closes a dense canopy fast and peaks high (NDVI ~0.8-0.85 at tasseling/silking, past
# mid-season), greening up ~0.6 from a bare ~0.2 baseline.
_MAIZE = CropTemplate(
    crop="maize",
    peak=Envelope(lo=0.65, hi=0.95, lo_falloff=0.45, hi_falloff=0.10),
    amplitude=Envelope(lo=0.40, hi=None, lo_falloff=0.40),
    peak_fraction=0.55,
    peak_fraction_tol=0.25,
)
# Tobacco: lush, deliberately N-rich canopy that peaks high then is topped/ripened; timing is more
# variable than maize, so a wider tolerance.
_TOBACCO = CropTemplate(
    crop="tobacco",
    peak=Envelope(lo=0.65, hi=0.95, lo_falloff=0.45, hi_falloff=0.10),
    amplitude=Envelope(lo=0.40, hi=None, lo_falloff=0.40),
    peak_fraction=0.50,
    peak_fraction_tol=0.30,
)
# Sorghum: drought-tolerant, smaller/sparser canopy and a lower peak (~0.7) than maize, so floors
# sit lower and the green-up is shallower.
_SORGHUM = CropTemplate(
    crop="sorghum",
    peak=Envelope(lo=0.50, hi=0.85, lo_falloff=0.40, hi_falloff=0.12),
    amplitude=Envelope(lo=0.30, hi=None, lo_falloff=0.30),
    peak_fraction=0.55,
    peak_fraction_tol=0.30,
)
# Cotton: slow canopy closure with bare inter-row soil, moderate absolute peak (~0.7-0.8) reached
# late at peak bloom/boll.
_COTTON = CropTemplate(
    crop="cotton",
    peak=Envelope(lo=0.50, hi=0.88, lo_falloff=0.40, hi_falloff=0.10),
    amplitude=Envelope(lo=0.30, hi=None, lo_falloff=0.30),
    peak_fraction=0.60,
    peak_fraction_tol=0.30,
)
# Fallow / bare: the inverse gate. The signal should stay flat and low; any real green-up (peak
# above the bare ceiling, or amplitude beyond noise) fails. Timing opts out (tol <= 0) since there
# is no expected peak.
_FALLOW = CropTemplate(
    crop="fallow",
    peak=Envelope(lo=None, hi=0.35, hi_falloff=0.35),
    amplitude=Envelope(lo=None, hi=0.15, hi_falloff=0.30),
    peak_fraction=0.5,
    peak_fraction_tol=0.0,
)

CROP_TEMPLATES: dict[str, CropTemplate] = {
    "maize": _MAIZE,
    "tobacco": _TOBACCO,
    "sorghum": _SORGHUM,
    "cotton": _COTTON,
    "fallow": _FALLOW,
}
