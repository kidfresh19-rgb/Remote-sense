"""Land-surface phenology from a field's NDVI time series (improvement plan Tier 1, T1.4): the
season's peak, greenup (start of season) and senescence (end of season), its length, amplitude, and
a time-integrated NDVI as a season-long productivity / biomass proxy.

Greenup and senescence are the dates the curve crosses a fraction of the seasonal amplitude above
the baseline (the half-amplitude convention by default), reported as observed pass dates: the method
never invents a date between passes. Crossings are returned as None when the season is not fully
observed (the series starts already green, or ends before senescence), so a partial series is read
honestly rather than guessed.

Pure: dates + values in, a `Phenology` out. No DB, no network, no crop model (crop-specific tuning
is the agronomist's knob, like the interpretation thresholds). Feeds the interpretation and alert
layers, which consume the resulting stages."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

# Half of the seasonal amplitude above the baseline: a widely used start/end-of-season threshold.
DEFAULT_THRESHOLD_FRACTION = 0.5


@dataclass(frozen=True)
class Phenology:
    """Phenology of one field-season. `start_of_season`/`end_of_season` are the observed greenup and
    senescence pass dates (None when not observed); `length_days` is their span; `time_integrated`
    is the trapezoidal integral of NDVI above the baseline in NDVI-days."""

    peak_date: date
    peak_value: float
    baseline: float
    amplitude: float
    start_of_season: date | None
    end_of_season: date | None
    length_days: int | None
    time_integrated: float


def phenology(
    dates: Sequence[date],
    ndvi: Sequence[float],
    *,
    threshold_fraction: float = DEFAULT_THRESHOLD_FRACTION,
) -> Phenology:
    """Derive phenology from parallel `dates` and `ndvi` sequences (one usable pass each). NaN/None
    values are dropped; the rest are sorted by date. Needs at least 3 valid observations."""
    pairs = sorted(
        (d, float(v))
        for d, v in zip(dates, ndvi, strict=True)
        if v is not None and not math.isnan(float(v))
    )
    if len(pairs) < 3:
        raise ValueError("phenology needs at least 3 valid NDVI observations")
    ds = [d for d, _ in pairs]
    vs = [v for _, v in pairs]

    peak_idx = max(range(len(vs)), key=lambda i: vs[i])
    peak_value = vs[peak_idx]
    baseline = min(vs)
    amplitude = peak_value - baseline
    threshold = baseline + threshold_fraction * amplitude

    # Greenup: the first at/above-threshold pass after a below-threshold pass, up to the peak.
    start_of_season: date | None = None
    below_seen = False
    for i in range(peak_idx + 1):
        if vs[i] < threshold:
            below_seen = True
        elif below_seen:
            start_of_season = ds[i]
            break

    # Senescence: the first at/below-threshold pass after the peak.
    end_of_season: date | None = None
    for i in range(peak_idx + 1, len(vs)):
        if vs[i] <= threshold:
            end_of_season = ds[i]
            break

    length_days: int | None = None
    if start_of_season is not None and end_of_season is not None:
        length_days = (end_of_season - start_of_season).days

    # Time-integrated NDVI above the baseline (trapezoid over days). Computed directly so it is
    # independent of the numpy trapz/trapezoid rename across versions.
    days = [(d - ds[0]).days for d in ds]
    above = [max(0.0, v - baseline) for v in vs]
    time_integrated = sum(
        (above[i] + above[i + 1]) / 2.0 * (days[i + 1] - days[i]) for i in range(len(days) - 1)
    )

    return Phenology(
        peak_date=ds[peak_idx],
        peak_value=peak_value,
        baseline=baseline,
        amplitude=amplitude,
        start_of_season=start_of_season,
        end_of_season=end_of_season,
        length_days=length_days,
        time_integrated=float(time_integrated),
    )
