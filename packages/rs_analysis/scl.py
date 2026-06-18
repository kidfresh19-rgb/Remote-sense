"""Per-AOI cloud masking via the Scene Classification Layer (SCL). Cloud is assessed per
field polygon from SCL, never from scene-level cloud % (CLAUDE.md invariant 3). The clear-
pixel fraction this produces is stored with every result so downstream consumers can flag
low-confidence passes honestly."""

from __future__ import annotations

from enum import IntEnum

import numpy as np


class SCL(IntEnum):
    """Sen2Cor Scene Classification Layer classes (L2A).

    Class 2 is labelled CAST_SHADOWS in current Sen2Cor (>= 2.08) and DARK_AREA_PIXELS in older
    docs; semantically it is low-reflectance terrain (topographic/dark-soil shadow) and is excluded
    from clear-surface stats either way. The integer mapping is fixed by Copernicus and is the
    contract every adapter reads against (SentiWiki S2 processing, Copernicus)."""

    NO_DATA = 0
    SATURATED_DEFECTIVE = 1
    DARK_AREA_PIXELS = 2  # CAST_SHADOWS in current Sen2Cor; kept for the historical alias
    CLOUD_SHADOWS = 3
    VEGETATION = 4
    NOT_VEGETATED = 5
    WATER = 6
    UNCLASSIFIED = 7
    CLOUD_MEDIUM_PROBABILITY = 8
    CLOUD_HIGH_PROBABILITY = 9
    THIN_CIRRUS = 10
    SNOW = 11


# Confirmed v1 masking decision (2026-06-18, agronomist review complete).
# Source: Sen2Cor scene-classification semantics (SentiWiki S2 processing, Copernicus) + standard
# agricultural-monitoring masking convention (e.g. ClearSKY/EOS SCL guidance).
#
# Kept as a usable observation of the land surface:
#   4 VEGETATION    - canopy; the signal we want.
#   5 NOT_VEGETATED - bare soil: pre-emergence, post-harvest, inter-row, or failed stand. Excluding
#                     it would bias the clear fraction high during fallow/early-season and hide
#                     genuine non-emergence, so it MUST count as a real observation.
#   6 WATER         - retained so clear_fraction is a generic "usable land-surface observation",
#                     consistent with invariant 3 and consumers such as ZINWA water layers. For
#                     crop-only vigour reads water pixels typically represent <1% of a field AOI;
#                     a crop-aware layer mask is the right place to exclude them, not here.
#   7 UNCLASSIFIED  - Sen2Cor could not assign a class; over cropland this is usually mixed/edge
#                     soil-vegetation, not cloud. Dropping it discards valid field pixels and
#                     understates coverage, so it is retained.
#
# NOTE: The Copernicus Browser Statistical tool defaults to {4, 5} only (no WATER, no
# UNCLASSIFIED), which is one structural source of divergence between remote-sense statistics and
# the Browser display. This is intentional: our base set is generic, not crop-only.
#
# Masked out (not a usable surface observation):
#   0 NO_DATA, 1 SATURATED_DEFECTIVE - no/garbage radiometry.
#   2 DARK_AREA_PIXELS / CAST_SHADOWS - shadowed/very-dark pixels with unreliable reflectance.
#   3 CLOUD_SHADOWS, 8/9 CLOUD_MED/HIGH, 10 THIN_CIRRUS - cloud-contaminated reflectance; the core
#     reason per-AOI masking exists (invariant 3). Cirrus is included because thin cloud still
#     depresses red/NIR and corrupts index values.
#   11 SNOW - effectively absent over Zimbabwean cropland; a "snow" flag there is almost always a
#     bright-cloud/edge misclassification, so excluding it is the safe choice.
CLEAR_CLASSES: frozenset[int] = frozenset(
    {SCL.VEGETATION, SCL.NOT_VEGETATED, SCL.WATER, SCL.UNCLASSIFIED}
)


def clear_mask(scl: np.ndarray) -> np.ndarray:
    """Boolean array: True where the SCL pixel is usable (a clear class)."""
    return np.isin(scl, list(CLEAR_CLASSES))


def clear_fraction(scl: np.ndarray, aoi_mask: np.ndarray | None = None) -> float:
    """Fraction of AOI pixels that are clear. `aoi_mask` (True = inside the field polygon)
    restricts the denominator to the field; without it the whole array is the AOI. NoData
    counts against the fraction - it is not a usable observation. Returns 0.0 for an empty
    AOI rather than dividing by zero."""
    clear = clear_mask(scl)
    if aoi_mask is not None:
        inside = np.count_nonzero(aoi_mask)
        if inside == 0:
            return 0.0
        return float(np.count_nonzero(clear & aoi_mask) / inside)
    total = scl.size
    if total == 0:
        return 0.0
    return float(np.count_nonzero(clear) / total)
