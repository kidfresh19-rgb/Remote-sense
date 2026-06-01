"""Per-AOI cloud masking via the Scene Classification Layer (SCL). Cloud is assessed per
field polygon from SCL, never from scene-level cloud % (CLAUDE.md invariant 3). The clear-
pixel fraction this produces is stored with every result so downstream consumers can flag
low-confidence passes honestly."""

from __future__ import annotations

from enum import IntEnum

import numpy as np


class SCL(IntEnum):
    """Sen2Cor Scene Classification Layer classes (L2A)."""

    NO_DATA = 0
    SATURATED_DEFECTIVE = 1
    DARK_AREA_PIXELS = 2
    CLOUD_SHADOWS = 3
    VEGETATION = 4
    NOT_VEGETATED = 5
    WATER = 6
    UNCLASSIFIED = 7
    CLOUD_MEDIUM_PROBABILITY = 8
    CLOUD_HIGH_PROBABILITY = 9
    THIN_CIRRUS = 10
    SNOW = 11


# ⚑ CONFIRM (agronomy review): the classes considered usable for index statistics. Vegetation,
# bare soil, water and unclassified are kept; nodata, saturated, dark/shadow, all cloud classes,
# cirrus and snow are masked out. This is the standard Sen2Cor agricultural convention and is
# the single domain knob most worth a second opinion from the agronomy-scientist.
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
