"""Per-AOI SCL masking tests (CLAUDE.md invariant 3): clear-class selection and the clear-
pixel fraction computed over the field footprint, not the scene."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis.scl import SCL, clear_fraction, clear_mask


def test_clear_mask_classes() -> None:
    scl = np.array(
        [
            SCL.VEGETATION,
            SCL.NOT_VEGETATED,
            SCL.WATER,
            SCL.UNCLASSIFIED,
            SCL.NO_DATA,
            SCL.CLOUD_SHADOWS,
            SCL.CLOUD_HIGH_PROBABILITY,
            SCL.THIN_CIRRUS,
            SCL.SNOW,
        ],
        dtype="uint8",
    )
    mask = clear_mask(scl)
    assert mask.tolist() == [True, True, True, True, False, False, False, False, False]


def test_clear_fraction_whole_array() -> None:
    scl = np.array([SCL.VEGETATION, SCL.VEGETATION, SCL.CLOUD_HIGH_PROBABILITY, SCL.NO_DATA])
    assert clear_fraction(scl) == pytest.approx(0.5)


def test_clear_fraction_restricted_to_aoi() -> None:
    scl = np.array([[SCL.VEGETATION, SCL.CLOUD_HIGH_PROBABILITY], [SCL.VEGETATION, SCL.SNOW]])
    aoi = np.array([[True, True], [False, False]])  # only the top row is inside the field
    # Inside AOI: one vegetation (clear), one cloud -> 1/2.
    assert clear_fraction(scl, aoi) == pytest.approx(0.5)


def test_clear_fraction_empty_aoi_is_zero() -> None:
    scl = np.array([[SCL.VEGETATION]])
    aoi = np.array([[False]])
    assert clear_fraction(scl, aoi) == 0.0
