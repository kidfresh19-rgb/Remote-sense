"""Tiler service (L5, Phase 4): serves colorized index map tiles, kept separate so map traffic
scales independently of the API (PLAN §2). A tile addresses a specific stored index COG
(field + scene + geometry version); the route resolves its object key, then rio-tiler windows and
renders it. Rendering needs the raster stack (`rasterio` / `rio-tiler`, the `geo` extra) which
installs only in the container, so the route returns 503 when it is absent and the service still
boots for health checks on a host without it."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from rs_analysis import get_colormap
from rs_core import S3CogStore, cog_key, gdal_s3_env, get_settings, vsis3_uri

from services.tiler.render import (
    RasterStackUnavailable,
    TileUnavailable,
    render_diff_tile,
    render_mask_tile,
    render_params,
    render_preview,
    render_tile,
)

app = FastAPI(title="remote-sense tiler", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/static/{index}/{geometry_version}/{field_id}/{scene_id}.jpg")
async def static_preview(
    index: str,
    geometry_version: int,
    field_id: str,
    scene_id: str,
) -> Response:
    """Full-extent JPEG natural-colour preview from a stored field COG (all overviews included).
    Used by the Passes tab thumbnail grid. 404 for an unknown index or missing COG; 503 when
    the raster stack is absent."""
    try:
        render_params(index)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown view {index!r}"
        ) from exc

    settings = get_settings()
    key = cog_key(
        field_id=field_id, scene_id=scene_id, index=index, geometry_version=geometry_version
    )
    source = vsis3_uri(settings.minio_bucket, key)
    try:
        jpeg = render_preview(source, index=index, gdal_env=gdal_s3_env(settings))
    except RasterStackUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "raster stack not installed (the `geo` extra runs in-container)",
        ) from exc
    except TileUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/export/{view}/{geometry_version}/{field_id}/{scene_id}.tif")
async def export_cog(
    view: str,
    geometry_version: int,
    field_id: str,
    scene_id: str,
) -> Response:
    """Raw COG byte passthrough for analyst download. No rendering: the bytes come straight from
    the object store. 422 for an unrecognized `view`; 404 when the COG is absent; 503 without the
    `storage` extra (boto3, in-container). The BFF generates a presigned MinIO URL instead of
    calling this route directly; this route exists as a fallback and for test convenience."""
    try:
        render_params(view)  # validates against the locked index + composite registry
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown view {view!r}"
        ) from exc

    settings = get_settings()
    key = cog_key(
        field_id=field_id, scene_id=scene_id, index=view, geometry_version=geometry_version
    )
    try:
        store = S3CogStore(settings)
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "object storage not available (the `storage` extra runs in-container)",
        ) from exc

    if not await run_in_threadpool(store.exists, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {view!r} COG for this pass")

    cog_bytes = await run_in_threadpool(store.get_bytes, key)
    filename = f"{field_id}_{scene_id}_{view}.tif"
    return Response(
        content=cog_bytes,
        media_type="image/tiff",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@app.get("/mask/{geometry_version}/{field_id}/{scene_id}/{z}/{x}/{y}.png")
async def mask_tile(
    geometry_version: int,
    field_id: str,
    scene_id: str,
    z: int,
    x: int,
    y: int,
) -> Response:
    """The cloud-honesty overlay tile (backlog 0046) for one field/scene/geometry version: a
    semi-transparent hatch over the pixels the per-AOI SCL mask (invariant 3) dropped, so the map
    can show *which part* of the field is unreliable, not just the `clear_fraction` badge. One mask
    per pass, so there is no `{index}` segment; the key pins field/scene/geometry version, so the
    overlay always tracks the active pass and can never show a stale mask. 404 when the pass has no
    stored mask COG or the tile is outside coverage; 503 when the raster stack is absent (host)."""
    settings = get_settings()
    key = cog_key(
        field_id=field_id, scene_id=scene_id, index="mask", geometry_version=geometry_version
    )
    source = vsis3_uri(settings.minio_bucket, key)
    try:
        png = render_mask_tile(source, z=z, x=x, y=y, gdal_env=gdal_s3_env(settings))
    except RasterStackUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "raster stack not installed (the `geo` extra runs in-container)",
        ) from exc
    except TileUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return Response(content=png, media_type="image/png")


@app.get("/diff/{index}/{geometry_version}/{field_id}/{scene_a}/{scene_b}/{z}/{x}/{y}.png")
async def diff_tile(
    index: str,
    geometry_version: int,
    field_id: str,
    scene_a: str,
    scene_b: str,
    z: int,
    x: int,
    y: int,
) -> Response:
    """The pass-to-pass difference tile (backlog 0045) for one field / index / geometry version:
    reads the index COGs of two scenes - `scene_a` (A, the baseline) and `scene_b` (B) - and renders
    B minus A on a symmetric diverging ramp centered on zero, so decline and growth read as opposite
    colours. Render-only: nothing new is stored (invariant 5 untouched). The diff only renders once
    the caller has picked both passes (SceneCompare's existing pickers); the route never guesses a
    pair. 404 for an unknown or non-diffable view (rgb / fcc / mask have no scalar diverging range),
    or when *either* pass has no COG / the tile is outside coverage (the same placeholder path a
    missing single-pass COG uses); 503 when the raster stack is absent (host)."""
    try:
        get_colormap(index)  # only scalar indices have a diverging range; rejects rgb/fcc/mask
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"cannot diff view {index!r}") from exc

    settings = get_settings()
    env = gdal_s3_env(settings)
    source_a = vsis3_uri(
        settings.minio_bucket,
        cog_key(
            field_id=field_id, scene_id=scene_a, index=index, geometry_version=geometry_version
        ),
    )
    source_b = vsis3_uri(
        settings.minio_bucket,
        cog_key(
            field_id=field_id, scene_id=scene_b, index=index, geometry_version=geometry_version
        ),
    )
    try:
        png = render_diff_tile(source_a, source_b, index=index, z=z, x=x, y=y, gdal_env=env)
    except RasterStackUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "raster stack not installed (the `geo` extra runs in-container)",
        ) from exc
    except TileUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return Response(content=png, media_type="image/png")
