"""Grounding: turn the stored analysis outputs for one field/pass into a structured, deterministic
evidence block. The interpretation model sees ONLY this - the numbers plus their band
classification - so it can explain but never invent (risk #6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from rs_analysis import AnalysisOutput, confidence_for

from rs_interpret.thresholds import classify


@dataclass(frozen=True)
class IndexReading:
    """One index's field-mean value and its band classification (None when no clear pixels)."""

    index: str
    mean: float | None
    band: str | None
    note: str | None


@dataclass(frozen=True)
class Evidence:
    """Everything the model is allowed to reason from for one field/pass."""

    crop: str | None
    pass_date: date | None
    clear_fraction: float
    confidence: str
    readings: list[IndexReading]


def _reading(output: AnalysisOutput, crop: str | None) -> IndexReading:
    mean = output.stats.mean
    if mean is None:
        return IndexReading(index=output.index_name, mean=None, band=None, note="no clear pixels")
    band = classify(output.index_name, mean, crop)
    return IndexReading(index=output.index_name, mean=mean, band=band.label, note=band.note)


def ground(
    outputs: Sequence[AnalysisOutput],
    *,
    crop: str | None = None,
    pass_date: date | None = None,
) -> Evidence:
    """Build the evidence block from one field/pass's per-index outputs. The pass-level clear
    fraction is the most conservative across indices (a cloudy 20 m index drags the whole pass's
    confidence down), and the confidence label is derived from it via the engine's thresholds."""
    if not outputs:
        raise ValueError("ground() needs at least one analysis output")
    clear = min(output.clear_fraction for output in outputs)
    return Evidence(
        crop=crop,
        pass_date=pass_date,
        clear_fraction=clear,
        confidence=confidence_for(clear),
        readings=[_reading(output, crop) for output in outputs],
    )
