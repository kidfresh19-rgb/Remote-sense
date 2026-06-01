"""The analysis engine: compose reflectance, per-AOI SCL masking, an index, range clipping and
zonal statistics into one stored result. The order is fixed (PLAN §5): reflectance first, mask
on SCL, compute the index, clip to range, exclude masked pixels from stats.

This module owns the math for the windowed_cog path, where reflectance-offset correctness must
be guaranteed. It is pure NumPy - no DB, no network, no rasterio - and is exercised end to end
by the validation matrix."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rs_analysis.indices import clip_to_range, get_index
from rs_analysis.reflectance import stack_to_reflectance
from rs_analysis.scl import clear_fraction, clear_mask
from rs_analysis.zonal import ZonalStats, zonal_stats

# ⚑ CONFIRM (agronomy review): clear-pixel-fraction thresholds that label a pass's confidence.
HIGH_CONFIDENCE_CLEAR = 0.8
MEDIUM_CONFIDENCE_CLEAR = 0.5


class ResolutionError(ValueError):
    """Raised when an index is asked to compute at a resolution finer than its coarsest band's
    native resolution - i.e. someone upsampled a 20 m band and called it 10 m (invariant 4)."""


@dataclass(frozen=True)
class AnalysisOutput:
    """One field, one pass, one index. The provider/scene provenance is attached by the caller
    when persisting; this carries the science: stats, the resolution actually computed at, the
    clear fraction and a derived confidence label."""

    index_name: str
    formula_version: str
    resolution_m: int
    clear_fraction: float
    confidence: str
    stats: ZonalStats


def confidence_for(clear: float) -> str:
    if clear >= HIGH_CONFIDENCE_CLEAR:
        return "high"
    if clear >= MEDIUM_CONFIDENCE_CLEAR:
        return "medium"
    return "low"


def _finite_fraction(values: np.ndarray, aoi_mask: np.ndarray | None) -> float:
    """Fraction of AOI pixels that are finite (not NaN/NoData). Used as the clear fraction
    only on the SCL-less path, where the adapter has already masked NoData to NaN."""
    finite = np.isfinite(values)
    if aoi_mask is not None:
        denom = int(np.count_nonzero(aoi_mask))
        return float(np.count_nonzero(finite & aoi_mask) / denom) if denom else 0.0
    return float(np.count_nonzero(finite) / values.size) if values.size else 0.0


def analyze_index(
    *,
    reflectance: dict[str, np.ndarray],
    index_name: str,
    resolution_m: int,
    scl: np.ndarray | None = None,
    aoi_mask: np.ndarray | None = None,
    clear_fraction_override: float | None = None,
) -> AnalysisOutput:
    """Compute one index over a field from reflectance bands on a single grid at
    `resolution_m`. `aoi_mask` (True = inside the field polygon) restricts statistics to the
    field. Enforces resolution honesty: the grid must be at the index's native resolution,
    never finer.

    Masking has two paths:
      * `scl` given -> per-AOI cloud masking from the SCL band and an SCL-derived clear
        fraction. This is the path the stored windowed_cog pipeline MUST use (invariant 3).
      * `scl` is None -> the adapter has already masked NoData/cloud to NaN (the mock and
        server_compute paths); the clear fraction comes from `clear_fraction_override` (the
        adapter's reported value) or, failing that, the finite-pixel fraction."""
    spec = get_index(index_name)
    if resolution_m < spec.resolution_m:
        raise ResolutionError(
            f"{spec.name} is a {spec.resolution_m} m index; refusing to compute it on a "
            f"{resolution_m} m grid (would present upsampled data as finer than it is)"
        )

    raw = spec.compute(reflectance)
    clipped = clip_to_range(raw, *spec.valid_range)

    if scl is not None:
        include = clear_mask(scl)
        if aoi_mask is not None:
            include = include & aoi_mask
        clear = clear_fraction(scl, aoi_mask)
    else:
        include = aoi_mask  # None -> all pixels; zonal_stats drops NaN either way
        clear = (
            clear_fraction_override
            if clear_fraction_override is not None
            else _finite_fraction(clipped, aoi_mask)
        )

    stats = zonal_stats(clipped, include)
    return AnalysisOutput(
        index_name=spec.name,
        formula_version=spec.formula_version,
        resolution_m=resolution_m,
        clear_fraction=clear,
        confidence=confidence_for(clear),
        stats=stats,
    )


def analyze_from_dn(
    *,
    dn_bands: dict[str, np.ndarray],
    add_offset: dict[str, float] | float,
    quantification: float,
    scl: np.ndarray,
    index_name: str,
    resolution_m: int,
    aoi_mask: np.ndarray | None = None,
) -> AnalysisOutput:
    """End-to-end from raw digital numbers: apply the per-scene reflectance offset, then run
    the index. This is the path the windowed_cog adapter takes, and the one the validation
    matrix checks against the Copernicus Browser - so the -1000 offset is provably applied."""
    reflectance = stack_to_reflectance(
        dn_bands, add_offset=add_offset, quantification=quantification
    )
    return analyze_index(
        reflectance=reflectance,
        scl=scl,
        index_name=index_name,
        resolution_m=resolution_m,
        aoi_mask=aoi_mask,
    )
