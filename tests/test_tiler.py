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


def test_tile_without_raster_stack_is_503(monkeypatch) -> None:
    # The 503 path is the host case (no geo extra). Force it deterministically by making the
    # renderer raise RasterStackUnavailable, so the assertion holds whether or not rasterio is
    # importable in the runner (it is in the geo container, now that libexpat is present).
    import services.tiler.main as tiler_main
    from services.tiler.render import RasterStackUnavailable

    def _no_stack(*args: object, **kwargs: object) -> bytes:
        raise RasterStackUnavailable("no raster stack")

    monkeypatch.setattr(tiler_main, "render_tile", _no_stack)
    with TestClient(app) as client:
        assert client.get("/tiles/ndvi/1/FIELD-1/SCENE-1/0/0/0.png").status_code == 503


def test_tile_missing_cog_is_404(monkeypatch) -> None:
    # With the raster stack present, a tile whose COG is absent or out of coverage is a 404.
    import services.tiler.main as tiler_main
    from services.tiler.render import TileUnavailable

    def _missing(*args: object, **kwargs: object) -> bytes:
        raise TileUnavailable("no COG at the source")

    monkeypatch.setattr(tiler_main, "render_tile", _missing)
    with TestClient(app) as client:
        assert client.get("/tiles/ndvi/1/FIELD-1/SCENE-1/0/0/0.png").status_code == 404
