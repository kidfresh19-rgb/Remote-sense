"""Tests for the tiler (L5): the pure tile/window + render-param math runs on the host; the
service boots and the tile route degrades to 503 without the raster stack (in-container)."""

from __future__ import annotations

from starlette.testclient import TestClient

from services.tiler.main import app
from services.tiler.render import render_params
from services.tiler.tiles import tile_to_bbox


def test_tile_to_bbox_whole_world() -> None:
    min_lon, min_lat, max_lon, max_lat = tile_to_bbox(0, 0, 0)
    assert min_lon == -180.0
    assert max_lon == 180.0
    assert round(max_lat, 2) == 85.05
    assert round(min_lat, 2) == -85.05


def test_tile_to_bbox_nw_quadrant() -> None:
    min_lon, min_lat, max_lon, max_lat = tile_to_bbox(1, 0, 0)
    assert min_lon == -180.0
    assert max_lon == 0.0
    assert round(min_lat, 2) == 0.0
    assert round(max_lat, 2) == 85.05


def test_render_params_from_locked_colormap() -> None:
    params = render_params("ndvi")
    assert params["colormap_name"] == "RdYlGn"
    assert params["rescale"] == (-0.2, 0.9)


def test_healthz() -> None:
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_tile_unknown_index_is_404() -> None:
    with TestClient(app) as client:
        # The index is validated before any raster work, so this is 404 even without the stack.
        assert client.get("/tiles/bogus/1/FIELD-1/SCENE-1/0/0/0.png").status_code == 404


def test_tile_without_raster_stack_is_503() -> None:
    with TestClient(app) as client:
        # rio-tiler (the geo extra) is not installed on the host -> the route degrades to 503.
        assert client.get("/tiles/ndvi/1/FIELD-1/SCENE-1/0/0/0.png").status_code == 503
