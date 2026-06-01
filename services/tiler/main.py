"""Tiler service (L5, Phase 4): serves index-COG previews + XYZ map tiles, kept separate so map
traffic scales independently of the API (PLAN §2). Rendering needs the raster stack
(`rasterio` / `rio-tiler`, the `geo` extra), which installs only in the container - the tile route
imports it lazily and returns 503 when it is absent, so the service still boots for health checks
on a host without it. Reading the actual COG window lands with COG emission (D1)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, status

from services.tiler.render import render_params
from services.tiler.tiles import tile_to_bbox

app = FastAPI(title="remote-sense tiler", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tiles/{index}/{z}/{x}/{y}.png")
async def tile(index: str, z: int, x: int, y: int) -> Response:
    """An index map tile. Validates the index + computes the window now; the COG read needs the
    raster stack (503 if absent) and emitted COGs (D1, 501 until then)."""
    try:
        render_params(index)  # validates the index against the locked colormaps
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown index {index!r}") from exc
    bbox = tile_to_bbox(z, x, y)
    try:
        import rio_tiler  # noqa: F401 - lazy: the `geo` extra, installed only in-container
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "raster stack not installed (the `geo` extra runs in-container)",
        ) from exc
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        f"COG read for {index} over {bbox} lands with COG emission (D1)",
    )
