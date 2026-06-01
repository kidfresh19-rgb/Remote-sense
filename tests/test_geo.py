"""Pure geospatial-logic tests for ingestion: UTM zone selection, geometry validation and
repair, the field-within-farm nesting tolerance (DI-3) and boundary-equivalence (DI-5). Zero
DB, zero network - synthetic Zimbabwean polygons only."""

from __future__ import annotations

import pytest
from rs_core.geo import (
    UTM_35S_EPSG,
    UTM_36S_EPSG,
    area_m2,
    geometries_equivalent,
    is_nested,
    nesting_fraction_outside,
    parse_epsg,
    to_shape,
    utm_epsg_for,
    validate_geometry,
)


def _square(center_lon: float, center_lat: float, side_deg: float) -> dict:
    half = side_deg / 2
    lon, lat = center_lon, center_lat
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - half, lat - half],
                [lon + half, lat - half],
                [lon + half, lat + half],
                [lon - half, lat + half],
                [lon - half, lat - half],
            ]
        ],
    }


# Harare sits east of the 30°E split, Bulawayo west of it.
_HARARE = (31.05, -17.83)
_BULAWAYO = (28.58, -20.15)


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (*_HARARE, UTM_36S_EPSG),
        (*_BULAWAYO, UTM_35S_EPSG),
        (30.0, -18.0, UTM_36S_EPSG),  # exactly on the split -> east zone
        (29.999, -18.0, UTM_35S_EPSG),
    ],
)
def test_utm_zone_selection(lon: float, lat: float, expected: int) -> None:
    assert utm_epsg_for(lon, lat) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("EPSG:4326", 4326), ("epsg:32736", 32736), ("4326", 4326), (32735, 32735)],
)
def test_parse_epsg(raw, expected) -> None:
    assert parse_epsg(raw) == expected


def test_parse_epsg_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_epsg("WGS84")


def test_validate_accepts_clean_polygon() -> None:
    result = validate_geometry(_square(*_HARARE, 0.01), src_epsg=4326)
    assert result.ok
    assert result.geometry is not None
    assert not result.repaired
    # ~0.01° near -17.8° latitude is roughly a 1.1 km square -> on the order of 1e6 m².
    assert 5e5 < area_m2(result.geometry) < 2e6


def test_validate_rejects_non_polygon() -> None:
    result = validate_geometry({"type": "Point", "coordinates": [31.0, -17.8]}, src_epsg=4326)
    assert not result.ok
    assert "polygonal" in (result.reason or "")


def test_validate_rejects_degenerate_area() -> None:
    tiny = _square(*_HARARE, 1e-7)  # sub-metre square
    result = validate_geometry(tiny, src_epsg=4326, min_area_m2=1.0)
    assert not result.ok
    assert "area" in (result.reason or "")


def test_validate_repairs_self_intersection() -> None:
    bowtie = {
        "type": "Polygon",
        "coordinates": [
            [
                [31.00, -17.80],
                [31.01, -17.81],
                [31.01, -17.80],
                [31.00, -17.81],
                [31.00, -17.80],
            ]
        ],
    }
    result = validate_geometry(bowtie, src_epsg=4326)
    assert result.ok
    assert result.repaired
    assert result.warnings and "repaired" in result.warnings[0]


def test_validate_reprojects_to_wgs84() -> None:
    # A geometry declared in UTM 36S should come back in WGS84 (lon/lat) near Harare.
    utm_square = {
        "type": "Polygon",
        "coordinates": [
            [
                [300000, 8030000],
                [300500, 8030000],
                [300500, 8030500],
                [300000, 8030500],
                [300000, 8030000],
            ]
        ],
    }
    result = validate_geometry(utm_square, src_epsg=UTM_36S_EPSG)
    assert result.ok
    minx, miny, maxx, maxy = result.geometry.bounds  # type: ignore[union-attr]
    assert 28 < minx < 34 and -20 < miny < -16


def test_nesting_inside_is_zero() -> None:
    farm = to_shape(_square(*_HARARE, 0.02))
    field = to_shape(_square(*_HARARE, 0.005))
    assert nesting_fraction_outside(field, farm) == pytest.approx(0.0, abs=1e-9)
    assert is_nested(field, farm)


def test_nesting_small_overhang_within_tolerance() -> None:
    farm = to_shape(_square(*_HARARE, 0.02))
    # Shift a small field so a sliver pokes out past the eastern edge.
    field = to_shape(_square(_HARARE[0] + 0.0099, _HARARE[1], 0.005))
    frac = nesting_fraction_outside(field, farm)
    assert 0.0 < frac < 0.5
    assert is_nested(field, farm, tolerance=frac + 0.01)
    assert not is_nested(field, farm, tolerance=max(frac - 0.01, 0.0))


def test_nesting_field_outside_fails() -> None:
    farm = to_shape(_square(*_HARARE, 0.02))
    field = to_shape(_square(_HARARE[0] + 0.1, _HARARE[1], 0.005))  # well clear of the farm
    assert nesting_fraction_outside(field, farm) == pytest.approx(1.0, abs=1e-6)
    assert not is_nested(field, farm)


def test_geometries_equivalent_identical() -> None:
    a = to_shape(_square(*_HARARE, 0.01))
    b = to_shape(_square(*_HARARE, 0.01))
    assert geometries_equivalent(a, b)


def test_geometries_equivalent_ignores_float_noise() -> None:
    a = to_shape(_square(*_HARARE, 0.01))
    jittered = _square(_HARARE[0] + 1e-7, _HARARE[1], 0.01)  # ~1 cm shift
    assert geometries_equivalent(a, to_shape(jittered))


def test_geometries_equivalent_detects_real_change() -> None:
    a = to_shape(_square(*_HARARE, 0.01))
    bigger = to_shape(_square(*_HARARE, 0.015))  # 50% larger boundary
    assert not geometries_equivalent(a, bigger)
