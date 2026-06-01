"""Index registry + formula tests. The locked band selection and native resolution per index
(PLAN §5) and safe handling of zero denominators / NaN."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis.indices import INDICES, clip_to_range, get_index


def test_index_native_resolution() -> None:
    assert get_index("ndvi").resolution_m == 10
    assert get_index("evi2").resolution_m == 10
    assert get_index("savi").resolution_m == 10
    assert get_index("ndre").resolution_m == 20  # B05 is 20 m
    assert get_index("ndmi").resolution_m == 20  # B11 is 20 m


def test_locked_bands() -> None:
    assert get_index("ndre").bands == ("B08", "B05")  # never B07/B8A in a series
    assert get_index("ndmi").bands == ("B08", "B11")


def test_unknown_index_rejected() -> None:
    with pytest.raises(KeyError, match="unknown index"):
        get_index("evi")  # classic EVI deliberately not in the suite


def test_compute_missing_band_rejected() -> None:
    with pytest.raises(KeyError, match="missing reflectance bands"):
        get_index("ndvi").compute({"B08": np.array([0.4])})


def test_ndvi_known_value() -> None:
    refl = {"B08": np.array([0.4]), "B04": np.array([0.1])}
    np.testing.assert_allclose(get_index("ndvi").compute(refl), [0.6])


def test_zero_denominator_is_nan_not_inf() -> None:
    # B08 == B04 == 0 -> 0/0; must be NaN so the pixel is excluded, never inf.
    refl = {"B08": np.array([0.0]), "B04": np.array([0.0])}
    result = get_index("ndvi").compute(refl)
    assert np.isnan(result[0])


def test_clip_preserves_nan_and_bounds() -> None:
    arr = np.array([-2.0, 0.5, 2.0, np.nan])
    clipped = clip_to_range(arr, -1.0, 1.0)
    assert clipped[0] == -1.0
    assert clipped[1] == 0.5
    assert clipped[2] == 1.0
    assert np.isnan(clipped[3])


def test_all_core_indices_registered() -> None:
    assert set(INDICES) == {"ndvi", "evi2", "savi", "ndre", "ndmi"}
