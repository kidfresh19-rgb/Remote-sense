"""Tests for rs_core.proxy_aoi (backlog 0028). Pure: zero DB, zero network.
Area tolerance 1 % - reprojection round-trip and coordinate quantisation cannot
move a Zimbabwe-scale square by more than a fraction of that."""

from __future__ import annotations

import math

import pytest
from rs_core.proxy_aoi import (
    MIN_USABLE_PIXELS,
    SizeClass,
    proxy_aoi,
)
from shapely.geometry import shape

# Bulawayo: west of 30°E (UTM 35S)
BULAWAYO_LAT, BULAWAYO_LON = -20.15, 28.58
# Harare: east of 30°E (UTM 36S)
HARARE_LAT, HARARE_LON = -17.83, 31.05
# Points straddling the 30°E zone-split
WEST_OF_SPLIT = (-19.0, 29.999)
EAST_OF_SPLIT = (-19.0, 30.001)

_AREA_TOL = 0.01  # 1 %


def _area_ha(result) -> float:
    return result.area_m2 / 10_000


class TestSizeClasses:
    @pytest.mark.parametrize(
        "sc, expected_ha",
        [
            (SizeClass.BACKYARD, 0.10),
            (SizeClass.SMALL, 0.50),
            (SizeClass.MEDIUM, 2.00),
        ],
    )
    def test_area_within_tolerance(self, sc, expected_ha):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, sc)
        assert math.isclose(_area_ha(result), expected_ha, rel_tol=_AREA_TOL)

    def test_large_requires_area_ha(self):
        with pytest.raises(ValueError, match="area_ha"):
            proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.LARGE)

    def test_large_with_explicit_area(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.LARGE, area_ha=50.0)
        assert math.isclose(_area_ha(result), 50.0, rel_tol=_AREA_TOL)

    def test_large_zero_area_raises(self):
        with pytest.raises(ValueError):
            proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.LARGE, area_ha=0.0)

    def test_large_negative_area_raises(self):
        with pytest.raises(ValueError):
            proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.LARGE, area_ha=-1.0)

    def test_unknown_size_class_raises(self):
        with pytest.raises(ValueError):
            proxy_aoi(HARARE_LAT, HARARE_LON, "giant_farm")  # type: ignore[arg-type]

    def test_string_size_class_accepted(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, "medium")
        assert math.isclose(_area_ha(result), 2.0, rel_tol=_AREA_TOL)


class TestGeometrySource:
    def test_geometry_source_is_officer_proxy(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.BACKYARD)
        assert result.geometry_source == "officer_proxy"

    def test_geometry_is_valid_wgs84_polygon(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.MEDIUM)
        geom = shape(result.geometry)
        assert geom.geom_type == "Polygon"
        assert geom.is_valid
        lon_min, lat_min, lon_max, lat_max = geom.bounds
        assert -180 < lon_min < lon_max < 180
        assert -90 < lat_min < lat_max < 90

    def test_centroid_near_input_pin(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.MEDIUM)
        geom = shape(result.geometry)
        c = geom.centroid
        assert abs(c.y - HARARE_LAT) < 0.01
        assert abs(c.x - HARARE_LON) < 0.01


class TestUTMZone:
    def test_both_zones_produce_valid_geometry(self):
        west = proxy_aoi(BULAWAYO_LAT, BULAWAYO_LON, SizeClass.MEDIUM)
        east = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.MEDIUM)
        assert shape(west.geometry).is_valid
        assert shape(east.geometry).is_valid

    def test_zone_flip_at_30e_gives_correct_area(self):
        lat, _ = WEST_OF_SPLIT
        west = proxy_aoi(lat, 29.999, SizeClass.MEDIUM)
        east = proxy_aoi(lat, 30.001, SizeClass.MEDIUM)
        assert math.isclose(_area_ha(west), 2.0, rel_tol=_AREA_TOL)
        assert math.isclose(_area_ha(east), 2.0, rel_tol=_AREA_TOL)


class TestPixelQuality:
    def test_backyard_is_low_quality(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.BACKYARD)
        # 0.10 ha → side≈31.6 m, eroded≈11.6 m → < 1 pixel after erosion
        assert result.low_pixel_quality
        assert result.usable_pixel_count < MIN_USABLE_PIXELS

    def test_medium_has_usable_pixels(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.MEDIUM)
        # 2 ha → side≈141 m, eroded≈121 m → ~144 pixels
        assert not result.low_pixel_quality
        assert result.usable_pixel_count >= MIN_USABLE_PIXELS

    def test_pixel_count_grows_with_area(self):
        small = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.SMALL)
        medium = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.MEDIUM)
        assert medium.usable_pixel_count > small.usable_pixel_count

    def test_area_m2_matches_target(self):
        result = proxy_aoi(HARARE_LAT, HARARE_LON, SizeClass.MEDIUM)
        assert math.isclose(result.area_m2, 20_000.0, rel_tol=1e-9)
