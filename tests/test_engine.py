"""Analysis-engine tests: orchestration order, resolution honesty (invariant 4), per-AOI
masking and the confidence label derived from the clear-pixel fraction."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis.engine import (
    ResolutionError,
    analyze_index,
    confidence_for,
)
from rs_analysis.scl import SCL


def _refl(value_b08: float, value_b04: float, shape=(2, 2)) -> dict[str, np.ndarray]:
    return {
        "B08": np.full(shape, value_b08, dtype="float64"),
        "B04": np.full(shape, value_b04, dtype="float64"),
    }


def test_analyze_index_basic() -> None:
    scl = np.full((2, 2), SCL.VEGETATION, dtype="uint8")
    out = analyze_index(reflectance=_refl(0.4, 0.1), scl=scl, index_name="ndvi", resolution_m=10)
    assert out.index_name == "ndvi"
    assert out.formula_version == "1"
    assert out.resolution_m == 10
    assert out.clear_fraction == 1.0
    assert out.confidence == "high"
    assert out.stats.mean == pytest.approx(0.6)


def test_resolution_honesty_enforced() -> None:
    # NDRE is a 20 m index (B05); computing it on a 10 m grid means B05 was upsampled.
    refl = {
        "B08": np.full((2, 2), 0.4),
        "B05": np.full((2, 2), 0.2),
    }
    scl = np.full((2, 2), SCL.VEGETATION, dtype="uint8")
    with pytest.raises(ResolutionError, match="20 m index"):
        analyze_index(reflectance=refl, scl=scl, index_name="ndre", resolution_m=10)


def test_cloud_pixels_excluded_and_lower_confidence() -> None:
    scl = np.array(
        [[SCL.VEGETATION, SCL.CLOUD_HIGH_PROBABILITY], [SCL.VEGETATION, SCL.CLOUD_SHADOWS]],
        dtype="uint8",
    )
    out = analyze_index(reflectance=_refl(0.4, 0.1), scl=scl, index_name="ndvi", resolution_m=10)
    assert out.stats.count == 2  # only the two clear pixels
    assert out.clear_fraction == pytest.approx(0.5)
    assert out.confidence == "medium"


def test_aoi_mask_restricts_statistics() -> None:
    scl = np.full((2, 2), SCL.VEGETATION, dtype="uint8")
    aoi = np.array([[True, False], [False, False]])
    out = analyze_index(
        reflectance=_refl(0.4, 0.1), scl=scl, index_name="ndvi", resolution_m=10, aoi_mask=aoi
    )
    assert out.stats.count == 1


@pytest.mark.parametrize(
    ("clear", "expected"),
    [(0.95, "high"), (0.8, "high"), (0.6, "medium"), (0.5, "medium"), (0.3, "low")],
)
def test_confidence_thresholds(clear, expected) -> None:
    assert confidence_for(clear) == expected


def test_scl_less_path_uses_clear_fraction_override() -> None:
    # The mock / server_compute path: no SCL band, adapter supplies the clear fraction.
    out = analyze_index(
        reflectance=_refl(0.4, 0.1),
        index_name="ndvi",
        resolution_m=10,
        clear_fraction_override=0.5,
    )
    assert out.clear_fraction == pytest.approx(0.5)
    assert out.confidence == "medium"
    assert out.stats.mean == pytest.approx(0.6)


def test_scl_less_path_excludes_nan_and_derives_fraction() -> None:
    # One of four pixels is NoData (NaN) from the adapter; no SCL, no override.
    b08 = np.array([[0.4, np.nan], [0.4, 0.4]])
    b04 = np.array([[0.1, np.nan], [0.1, 0.1]])
    out = analyze_index(reflectance={"B08": b08, "B04": b04}, index_name="ndvi", resolution_m=10)
    assert out.stats.count == 3
    assert out.clear_fraction == pytest.approx(0.75)  # 3 of 4 pixels finite
