"""The validation matrix - the sacred test (CLAUDE.md §3). Every index is checked numerically
against an external reference on known inputs, and reflectance-offset handling is the #1 thing
it verifies.

Two layers:
  1. Exact synthetic reference: known DN + scene metadata -> hand-computed reflectance ->
     hand-computed index. Proves the formulas and, critically, that the -1000 Baseline-04.00
     offset is actually applied end to end (a with-offset vs without-offset assertion).
  2. Real Copernicus Browser scenes: live-gated in `test_validation_matrix_live.py` (added
     2026-06-04 once windowed_cog went live), asserting windowed_cog's AOI-mean index values match
     the CDSE Process API (the Browser's own engine) within 0.01 on real Zimbabwe scenes.
"""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis import analyze_from_dn

# Sentinel-2 L2A baseline 04.00.
QUANT = 10000.0
OFFSET = -1000.0


def _scene(dn: dict[str, int], shape=(2, 2)) -> dict[str, np.ndarray]:
    return {band: np.full(shape, value, dtype="float64") for band, value in dn.items()}


# Each row: DN per band, the reflectance it must convert to, and the expected index values.
# Reflectance: rho = (DN - 1000) / 10000. Index values hand-computed from those reflectances.
VALIDATION_MATRIX = [
    {
        "label": "healthy vegetation",
        "dn": {"B08": 5000, "B04": 2000, "B05": 3000, "B11": 4000},
        # reflectance: B08=0.4 B04=0.1 B05=0.2 B11=0.3
        "expected": {
            "ndvi": 0.6,  # (0.4-0.1)/(0.4+0.1)
            "evi2": 0.75 / 1.64,  # 2.5*0.3 / (0.4 + 0.24 + 1)
            "savi": 0.45,  # (0.3/1.0)*1.5
            "ndre": 0.2 / 0.6,  # (0.4-0.2)/(0.4+0.2)
            "ndmi": 0.1 / 0.7,  # (0.4-0.3)/(0.4+0.3)
        },
    },
    {
        "label": "bare / stressed",
        "dn": {"B08": 3000, "B04": 2500, "B05": 2800, "B11": 3200},
        # reflectance: B08=0.2 B04=0.15 B05=0.18 B11=0.22
        "expected": {
            "ndvi": 0.05 / 0.35,  # (0.2-0.15)/(0.2+0.15)
            "evi2": (2.5 * 0.05) / (0.2 + 2.4 * 0.15 + 1.0),
            "savi": (0.05 / 0.85) * 1.5,
            "ndre": (0.2 - 0.18) / (0.2 + 0.18),
            "ndmi": (0.2 - 0.22) / (0.2 + 0.22),
        },
    },
]

_RES = {"ndvi": 10, "evi2": 10, "savi": 10, "ndre": 20, "ndmi": 20}


@pytest.mark.parametrize("entry", VALIDATION_MATRIX, ids=[e["label"] for e in VALIDATION_MATRIX])
def test_index_values_match_reference(entry) -> None:
    bands = _scene(entry["dn"])
    scl = np.full((2, 2), 4, dtype="uint8")  # all VEGETATION -> all clear
    for index_name, expected in entry["expected"].items():
        out = analyze_from_dn(
            dn_bands=bands,
            add_offset=OFFSET,
            quantification=QUANT,
            scl=scl,
            index_name=index_name,
            resolution_m=_RES[index_name],
        )
        assert out.stats.mean == pytest.approx(expected, abs=1e-6), (
            f"{index_name} ({entry['label']})"
        )
        assert out.clear_fraction == 1.0


def test_offset_is_actually_applied() -> None:
    """The defining check: applying the -1000 offset materially changes the result. If the
    offset were silently dropped, NDVI would read 0.4286 here instead of 0.6, and EVI2/SAVI
    would be biased far more. This is the failure mode the whole matrix exists to catch."""
    bands = _scene({"B08": 5000, "B04": 2000})
    scl = np.full((2, 2), 4, dtype="uint8")

    with_offset = analyze_from_dn(
        dn_bands=bands,
        add_offset=OFFSET,
        quantification=QUANT,
        scl=scl,
        index_name="ndvi",
        resolution_m=10,
    ).stats.mean
    without_offset = analyze_from_dn(
        dn_bands=bands,
        add_offset=0.0,
        quantification=QUANT,
        scl=scl,
        index_name="ndvi",
        resolution_m=10,
    ).stats.mean

    assert with_offset == pytest.approx(0.6, abs=1e-6)
    assert without_offset == pytest.approx((0.5 - 0.2) / (0.5 + 0.2), abs=1e-6)
    assert abs(with_offset - without_offset) > 0.15  # offset is not a rounding detail


def test_nodata_excluded_from_stats() -> None:
    """DN == 0 is NoData; it must not be read as zero reflectance and dragged into the mean."""
    b08 = np.array([[5000, 0], [5000, 5000]], dtype="float64")
    b04 = np.array([[2000, 0], [2000, 2000]], dtype="float64")
    scl = np.full((2, 2), 4, dtype="uint8")
    out = analyze_from_dn(
        dn_bands={"B08": b08, "B04": b04},
        add_offset=OFFSET,
        quantification=QUANT,
        scl=scl,
        index_name="ndvi",
        resolution_m=10,
    )
    assert out.stats.count == 3  # the NoData pixel is dropped
    assert out.stats.mean == pytest.approx(0.6, abs=1e-6)
