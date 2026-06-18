"""Pure region-cluster geometry (comparison groups, PRD 0002 slice 1): centroid assignment + the
boundary-adjacent flag against synthetic polygons, MultiPolygon coercion, and the layer loader's
skip-and-report and error contract. Zero DB, zero network (prior art test_geo.py). The loader tests
need geopandas (the `geo` extra) and skip when absent; the assignment tests need only shapely."""

from __future__ import annotations

import json
import uuid

import pytest
from rs_core.rbac import Permission, Role, permissions_for
from rs_core.regions import (
    RegionLayerError,
    as_multipolygon,
    assign_centroid,
    natural_region_composition,
    read_region_layer,
)
from shapely.geometry import MultiPolygon, Polygon

# Two adjacent synthetic regions over a Zimbabwe-ish extent, sharing the 31°E edge. Labels are
# arbitrary; this exercises the algorithm, never real agronomy.
_A_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
_B_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


def _rect(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> Polygon:
    return Polygon(
        [
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
        ]
    )


def _candidates() -> list[tuple[uuid.UUID, Polygon]]:
    return [
        (_A_ID, _rect(29.0, -18.0, 31.0, -16.0)),
        (_B_ID, _rect(31.0, -18.0, 33.0, -16.0)),
    ]


def test_assign_centroid_contains() -> None:
    a = assign_centroid(30.0, -17.0, _candidates(), edge_tolerance_m=250.0)
    b = assign_centroid(32.0, -17.0, _candidates(), edge_tolerance_m=250.0)
    assert a is not None and a.region_boundary_id == _A_ID
    assert b is not None and b.region_boundary_id == _B_ID
    assert a.boundary_adjacent is False  # deep in the interior


def test_assign_centroid_outside_is_none() -> None:
    assert assign_centroid(40.0, -17.0, _candidates(), edge_tolerance_m=250.0) is None


def test_assign_centroid_boundary_adjacent() -> None:
    # ~106 m west of the shared 31°E edge at lat -17: inside Region A, within a 250 m tolerance.
    near = assign_centroid(30.999, -17.0, _candidates(), edge_tolerance_m=250.0)
    assert near is not None and near.region_boundary_id == _A_ID
    assert near.boundary_adjacent is True
    # Same point, a tighter tolerance: no longer flagged.
    tight = assign_centroid(30.999, -17.0, _candidates(), edge_tolerance_m=50.0)
    assert tight is not None and tight.boundary_adjacent is False


def test_assign_centroid_is_deterministic_on_shared_edge() -> None:
    # A point on the shared edge is covered by both regions; the stable id order always wins,
    # regardless of the candidate order the caller passes (_A_ID < _B_ID).
    first = assign_centroid(31.0, -17.0, _candidates(), edge_tolerance_m=250.0)
    second = assign_centroid(31.0, -17.0, list(reversed(_candidates())), edge_tolerance_m=250.0)
    assert first is not None and second is not None
    assert first.region_boundary_id == second.region_boundary_id == _A_ID


def test_as_multipolygon() -> None:
    poly = _rect(0.0, 0.0, 1.0, 1.0)
    assert isinstance(as_multipolygon(poly), MultiPolygon)
    mp = MultiPolygon([poly])
    assert as_multipolygon(mp) is mp


# --- loader (needs geopandas) ---------------------------------------------------------------

_GOOD = [[[30.0, -18.0], [31.0, -18.0], [31.0, -17.0], [30.0, -17.0], [30.0, -18.0]]]
# ~0.11 m on a side: area well below the 1 m² validation floor, so it is skipped as degenerate.
_TINY = [
    [
        [30.0, -17.0],
        [30.000001, -17.0],
        [30.000001, -17.000001],
        [30.0, -17.000001],
        [30.0, -17.0],
    ]
]


def _feature(name: str | None, coords: list | None) -> dict:
    geom = None if coords is None else {"type": "Polygon", "coordinates": coords}
    props = {} if name is None else {"gez_name": name}
    return {"type": "Feature", "properties": props, "geometry": geom}


def _write_geojson(path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def test_read_region_layer_happy(tmp_path) -> None:
    pytest.importorskip("geopandas")
    path = tmp_path / "regions.geojson"
    second = [[[31.0, -18.0], [32.0, -18.0], [32.0, -17.0], [31.0, -17.0], [31.0, -18.0]]]
    _write_geojson(path, [_feature("Region III", _GOOD), _feature("Region IV", second)])
    loaded = read_region_layer(path, name_column="gez_name")
    assert loaded.source_crs == "EPSG:4326"
    assert [f.name for f in loaded.features] == ["Region III", "Region IV"]
    assert all(isinstance(f.geometry, MultiPolygon) for f in loaded.features)
    assert loaded.skipped == []


def test_read_region_layer_skips_and_reports(tmp_path) -> None:
    pytest.importorskip("geopandas")
    path = tmp_path / "regions.geojson"
    _write_geojson(
        path,
        [
            _feature("Region III", _GOOD),
            _feature(None, _GOOD),  # missing name -> skipped, not fatal
            _feature("Tiny", _TINY),  # degenerate area -> skipped, not fatal
        ],
    )
    loaded = read_region_layer(path, name_column="gez_name")
    assert [f.name for f in loaded.features] == ["Region III"]
    assert len(loaded.skipped) == 2
    assert any("name" in s.reason for s in loaded.skipped)


def test_read_region_layer_missing_name_column(tmp_path) -> None:
    pytest.importorskip("geopandas")
    path = tmp_path / "regions.geojson"
    _write_geojson(path, [_feature("Region III", _GOOD)])
    with pytest.raises(RegionLayerError):
        read_region_layer(path, name_column="NOT_A_COLUMN")


# --- Natural Region composition + RBAC ------------------------------------------------------


def test_natural_region_composition_single() -> None:
    nr = [("Region III", _rect(30.0, -18.0, 32.0, -16.0))]
    composition, dominant = natural_region_composition(_rect(30.5, -17.5, 31.0, -17.0), nr)
    assert dominant == "Region III"
    assert composition == {"Region III": 1.0}


def test_natural_region_composition_spans_two_regions() -> None:
    nr = [
        ("Region III", _rect(29.0, -18.0, 31.0, -16.0)),
        ("Region IV", _rect(31.0, -18.0, 33.0, -16.0)),
    ]
    # Straddles the 31°E boundary: ~0.6 deg west (III), ~0.4 deg east (IV). Never clipped.
    composition, dominant = natural_region_composition(_rect(30.4, -17.5, 31.4, -17.0), nr)
    assert set(composition) == {"Region III", "Region IV"}
    assert dominant == "Region III"
    assert composition["Region III"] > composition["Region IV"]
    assert abs(sum(composition.values()) - 1.0) < 0.05


def test_natural_region_composition_outside_and_empty() -> None:
    nr = [("Region III", _rect(30.0, -18.0, 32.0, -16.0))]
    assert natural_region_composition(_rect(40.0, -17.5, 41.0, -17.0), nr) == ({}, None)
    assert natural_region_composition(_rect(30.5, -17.5, 31.0, -17.0), []) == ({}, None)


def test_region_rbac_mappings() -> None:
    # Open Item 3 mappings: create_region_cluster -> analyst; upload_region_boundary -> admin only.
    analyst = permissions_for([Role.ANALYST])
    assert Permission.CREATE_REGION_CLUSTER in analyst
    assert Permission.UPLOAD_REGION_BOUNDARY not in analyst
    assert Permission.CREATE_REGION_CLUSTER not in permissions_for([Role.VIEWER])
    admin = permissions_for([Role.ADMIN])
    assert Permission.CREATE_REGION_CLUSTER in admin
    assert Permission.UPLOAD_REGION_BOUNDARY in admin
