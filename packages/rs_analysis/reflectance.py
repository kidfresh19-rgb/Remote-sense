"""Reflectance conversion - the single highest-risk correctness rule in the system
(CLAUDE.md invariant 2).

    ρ = (DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE

Both the offset and the quantification value are read per scene from metadata, never
hard-coded. For Sentinel-2 L2A processing baseline 04.00 (operational 2022-01-25) the offset
is -1000; the whole backfill window is post-2022 so it always applies. DN == 0 is NoData and
becomes NaN - never treated as zero reflectance, which would silently bias every index."""

from __future__ import annotations

import numpy as np

NODATA_DN = 0


def to_reflectance(
    dn: np.ndarray,
    *,
    add_offset: float,
    quantification: float,
) -> np.ndarray:
    """Convert raw digital numbers to surface reflectance. NoData (DN == 0) -> NaN.

    Computed in float64 for numerical headroom; callers may downcast. The offset is additive
    and typically negative (-1000), so a DN of 0 would map to a spurious negative reflectance
    if not masked - hence NoData is applied explicitly."""
    if quantification == 0:
        raise ValueError("quantification value must be non-zero")
    dn_arr = np.asarray(dn, dtype="float64")
    reflectance = (dn_arr + add_offset) / quantification
    return np.where(dn_arr == NODATA_DN, np.nan, reflectance)


def stack_to_reflectance(
    bands: dict[str, np.ndarray],
    *,
    add_offset: dict[str, float] | float,
    quantification: float,
) -> dict[str, np.ndarray]:
    """Convert a dict of DN band arrays to reflectance. `add_offset` may be a per-band dict
    (as carried in SceneMetadata.boa_add_offset) or a single scalar applied to every band."""
    out: dict[str, np.ndarray] = {}
    for band_id, dn in bands.items():
        offset = add_offset[band_id] if isinstance(add_offset, dict) else add_offset
        out[band_id] = to_reflectance(dn, add_offset=offset, quantification=quantification)
    return out
