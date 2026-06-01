"""Tiler service (L5, Phase 4): serves colorized index map tiles, kept separate so map traffic
scales independently of the API (PLAN §2). A tile addresses a specific stored index COG
(field + scene + geometry version); the route resolves its object key, then rio-tiler windows and
renders it. Rendering needs the raster stack (`rasterio` / `rio-tiler`, the `geo` extra) which
installs only in the container, so the route returns 503 when it is absent and the service still
boots for health checks on a host without it."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, status
from rs_core import cog_key, gdal_s3_env, get_settings, vsis3_uri

from services.tiler.render import (
    RasterStackUnavailable,
    TileUnavailable,
    render_params,
    render_tile,
)

app = FastAPI(title="remote-sense tiler", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tiles/{index}/{geometry_version}/{field_id}/{scene_id}/{z}/{x}/{y}.png")
async def tile(
    index: str,
    geometry_version: int,
    field_id: str,
    scene_id: str,
    z: int,
    x: int,
    y: int,
) -> Response:
    """A colorized index tile for one field/scene/geometry version. 404 for an unknown index or a
    tile with no COG / outside coverage; 503 when the raster stack is absent (host)."""
    try:
        render_params(index)  # validates the index against the locked colormaps
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown index {index!r}") from exc

    settings = get_settings()
    key = cog_key(
        field_id=field_id, scene_id=scene_id, index=index, geometry_version=geometry_version
    )
    source = vsis3_uri(settings.minio_bucket, key)
    try:
        png = render_tile(source, index=index, z=z, x=x, y=y, gdal_env=gdal_s3_env(settings))
    except RasterStackUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "raster stack not installed (the `geo` extra runs in-container)",
        ) from exc
    except TileUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return Response(content=png, media_type="image/png")
