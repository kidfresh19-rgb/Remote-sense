"""Reflectance conversion tests (CLAUDE.md invariant 2). The offset/quantification come from
metadata; NoData (DN == 0) becomes NaN, never zero reflectance."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis.reflectance import stack_to_reflectance, to_reflectance


def test_basic_conversion() -> None:
    dn = np.array([[5000, 2000]], dtype="float64")
    refl = to_reflectance(dn, add_offset=-1000.0, quantification=10000.0)
    np.testing.assert_allclose(refl, [[0.4, 0.1]])


def test_nodata_becomes_nan_not_negative() -> None:
    # Without NoData handling, DN 0 with a -1000 offset would map to -0.1 reflectance.
    dn = np.array([0, 5000], dtype="float64")
    refl = to_reflectance(dn, add_offset=-1000.0, quantification=10000.0)
    assert np.isnan(refl[0])
    assert refl[1] == pytest.approx(0.4)


def test_zero_quantification_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        to_reflectance(np.array([1.0]), add_offset=-1000.0, quantification=0.0)


def test_negative_quantification_rejected() -> None:
    # A negative quantification would silently flip the sign of every reflectance value.
    with pytest.raises(ValueError, match="positive"):
        to_reflectance(np.array([1.0]), add_offset=-1000.0, quantification=-10000.0)


def test_nonfinite_quantification_rejected() -> None:
    # A NaN quantification would silently mask every pixel instead of raising.
    with pytest.raises(ValueError, match="positive"):
        to_reflectance(np.array([1.0]), add_offset=-1000.0, quantification=float("nan"))


def test_stack_rejects_missing_per_band_offset() -> None:
    # Per-band offsets come from scene metadata; a band without one should fail fast and name
    # the gap, not raise a bare KeyError deep in the conversion loop (invariant 2).
    bands = {"B04": np.array([2000.0]), "B08": np.array([5000.0])}
    with pytest.raises(ValueError, match="B08"):
        stack_to_reflectance(bands, add_offset={"B04": -1000.0}, quantification=10000.0)


def test_stack_applies_per_band_offset() -> None:
    bands = {"B04": np.array([2000.0]), "B08": np.array([5000.0])}
    refl = stack_to_reflectance(
        bands, add_offset={"B04": -1000.0, "B08": -1000.0}, quantification=10000.0
    )
    assert refl["B04"][0] == pytest.approx(0.1)
    assert refl["B08"][0] == pytest.approx(0.4)


def test_stack_accepts_scalar_offset() -> None:
    bands = {"B04": np.array([2000.0])}
    refl = stack_to_reflectance(bands, add_offset=-1000.0, quantification=10000.0)
    assert refl["B04"][0] == pytest.approx(0.1)
