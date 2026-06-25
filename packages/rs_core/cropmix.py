"""Pure crop-mix validation and dominant-crop derivation for Ward Watch enrollment (PRD 0003 §8.3,
backlog 0029). A communal plot is intercropped, so enrollment captures the FULL mix (every crop and
its share), even though v1 cohorts only on the dominant crop. This module is the gate that turns a
raw declared mix into a validated, normalised one with its dominant crop resolved.

The full mix is stored, never collapsed to the dominant at capture, so a later season can re-derive
the dominant under a different rule and the flywheel keeps the intercrop labels. No DB, no network -
a list of (crop, weight) in, a `ResolvedCropMix` out."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rs_core.crops import crop_rank, is_declared_crop, normalize_crop

# How far the declared shares may sum from 100 and still be accepted. Officer estimates of an
# intercropped plot are eyeballed, not surveyed, so an exact 100 is not required; a large gap is a
# data-entry error worth rejecting.
DEFAULT_WEIGHT_SUM_TOLERANCE = 1.0


@dataclass(frozen=True)
class CropWeight:
    """One crop's share of a plot, as a percentage (0, 100]."""

    crop: str
    weight_pct: float


@dataclass(frozen=True)
class ResolvedCropMix:
    """A validated crop mix: normalised entries in input order plus the resolved `dominant_crop`
    (the largest share, ties broken by the canonical crop order)."""

    entries: tuple[CropWeight, ...]
    dominant_crop: str


def resolve_crop_mix(
    mix: Sequence[CropWeight],
    *,
    weight_sum_tolerance: float = DEFAULT_WEIGHT_SUM_TOLERANCE,
) -> ResolvedCropMix:
    """Validate a declared crop mix and resolve its dominant crop.

    Every crop must be in the declared-crop vocabulary (`rs_core.crops`), each weight must be in
    (0, 100], no crop may repeat, and the weights must sum to within `weight_sum_tolerance` of 100.
    Crop names are normalised (trimmed, lowercased) in the result. The dominant crop is the largest
    weight, ties broken by canonical crop order so the result is deterministic.

    Raises ValueError on an empty mix, an unknown crop, a non-positive or over-100 weight, a
    repeated crop, or a weight sum outside the tolerance."""
    if not mix:
        raise ValueError("crop mix must have at least one entry")

    normalized: list[CropWeight] = []
    seen: set[str] = set()
    for entry in mix:
        crop = normalize_crop(entry.crop)
        if not is_declared_crop(crop):
            raise ValueError(f"unknown crop {entry.crop!r}: not in the declared-crop vocabulary")
        if crop in seen:
            raise ValueError(f"crop {crop!r} appears more than once in the mix")
        if not 0.0 < entry.weight_pct <= 100.0:
            raise ValueError(f"weight for {crop!r} must be in (0, 100], got {entry.weight_pct}")
        seen.add(crop)
        normalized.append(CropWeight(crop=crop, weight_pct=entry.weight_pct))

    total = sum(entry.weight_pct for entry in normalized)
    if abs(total - 100.0) > weight_sum_tolerance:
        raise ValueError(
            f"crop-mix weights must sum to ~100 (within {weight_sum_tolerance}), got {total}"
        )

    dominant = min(normalized, key=lambda e: (-e.weight_pct, crop_rank(e.crop))).crop
    return ResolvedCropMix(entries=tuple(normalized), dominant_crop=dominant)
