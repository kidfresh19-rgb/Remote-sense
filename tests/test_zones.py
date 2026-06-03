"""Tests for productivity/management zoning (Tier 1, T1.3). The k-means and zoning core is pure
NumPy, so it tests with synthetic stacks, zero network and zero DB. The polygon vectorisation needs
rasterio and is exercised in-container / CI."""

from __future__ import annotations

import numpy as np
import pytest
from rs_analysis.zones import NODATA_ZONE, kmeans, productivity_zones


def test_kmeans_separates_two_obvious_clusters():
    x = np.array([[0.0], [0.1], [5.0], [5.2]])
    labels, centroids = kmeans(x, 2, seed=0)
    # The two low points share a label distinct from the two high points.
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert centroids.shape == (2, 1)


def test_kmeans_is_deterministic_for_a_seed():
    x = np.array([[0.0], [0.2], [4.0], [4.1], [8.0], [8.3]])
    a, _ = kmeans(x, 3, seed=7)
    b, _ = kmeans(x, 3, seed=7)
    assert np.array_equal(a, b)


def _split_stack() -> np.ndarray:
    # 3 passes, 2 rows x 4 cols. Left two columns low NDVI (~0.2), right two high (~0.8).
    low = np.full((3, 2, 2), 0.2)
    high = np.full((3, 2, 2), 0.8)
    return np.concatenate([low, high], axis=2)  # shape (3, 2, 4)


def test_productivity_zones_orders_by_productivity():
    result = productivity_zones(_split_stack(), k=2, seed=0)
    assert result.k == 2
    # Left columns are the low zone (0), right columns the high zone (1): zones ordered by mean.
    assert (result.labels[:, :2] == 0).all()
    assert (result.labels[:, 2:] == 1).all()
    assert result.zone_means[0] < result.zone_means[1]
    assert result.zone_means[0] == pytest.approx(0.2)
    assert result.zone_means[1] == pytest.approx(0.8)
    assert result.zone_pixel_counts == [4, 4]


def test_productivity_zones_marks_unobserved_pixels_nodata():
    stack = _split_stack()
    stack[:, 0, 0] = np.nan  # this pixel was masked on every pass
    result = productivity_zones(stack, k=2, seed=0)
    assert result.labels[0, 0] == NODATA_ZONE
    # The masked pixel is excluded from the zone counts.
    assert sum(result.zone_pixel_counts) == 7


def test_productivity_zones_reduces_k_for_tiny_fields():
    # Only one valid pixel -> at most one zone.
    stack = np.full((2, 1, 1), 0.5)
    result = productivity_zones(stack, k=3, seed=0)
    assert result.k == 1
    assert result.labels[0, 0] == 0


def test_productivity_zones_all_masked_is_empty():
    stack = np.full((2, 2, 2), np.nan)
    result = productivity_zones(stack, k=3)
    assert result.k == 0
    assert (result.labels == NODATA_ZONE).all()
    assert result.zone_means == []
