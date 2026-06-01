"""Zonal-statistics tests: masked and non-finite pixels are excluded before any statistic is
computed (PLAN §5, rule 5)."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis.zonal import zonal_stats


def test_basic_stats() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    stats = zonal_stats(values)
    assert stats.count == 5
    assert stats.mean == pytest.approx(3.0)
    assert stats.min == pytest.approx(1.0)
    assert stats.max == pytest.approx(5.0)
    assert stats.std == pytest.approx(np.sqrt(2.0))  # population std
    assert stats.p10 == pytest.approx(1.4)
    assert stats.p90 == pytest.approx(4.6)


def test_include_mask_excludes_pixels() -> None:
    values = np.array([1.0, 100.0, 3.0])
    include = np.array([True, False, True])  # drop the outlier outside the field
    stats = zonal_stats(values, include)
    assert stats.count == 2
    assert stats.mean == pytest.approx(2.0)


def test_nan_excluded() -> None:
    values = np.array([1.0, np.nan, 3.0])
    stats = zonal_stats(values)
    assert stats.count == 2
    assert stats.mean == pytest.approx(2.0)


def test_empty_selection_returns_none_stats() -> None:
    values = np.array([np.nan, np.nan])
    stats = zonal_stats(values)
    assert stats.count == 0
    assert stats.mean is None
    assert stats.p90 is None
