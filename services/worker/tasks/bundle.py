"""All-passes orthophoto bundle task (orthophoto plan P4): zip one georeferenced RGB reflectance
GeoTIFF per usable pass of an AOI Studio run. Runs on the default bulk Celery queue (NOT the
`interactive` lane), so a dozens-of-passes render can never starve a user-facing AOI Studio preview.

Each pass reuses the per-scene RGB COG cache at `aoi_rgb_cog_key`: a pass whose thumbnail or GeoTIFF
was already viewed is an instant cache hit, and a freshly rendered one is written back so a later
single-pass download hits the cache too. A pass that cannot be rendered is skipped (logged), not
fatal, so a transient single-scene failure still yields a zip of everything that worked."""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any
from zipfile import ZIP_STORED, ZipFile

from rs_core import cog_store_from_settings, get_settings
from rs_core.geo import canonical_geometry_hash
from rs_core.logging import get_logger
from rs_core.storage import S3CogStore, aoi_rgb_cog_key
from rs_imagery import get_access_adapter

from services.worker.celery_app import celery
from services.worker.tasks.analysis import _render_clipped_rgb_cog

log = get_logger(__name__)


@celery.task(name="analysis.bundle_aoi_orthophotos", bind=True)
def bundle_aoi_orthophotos_task(
    self: Any,
    geometry: dict[str, Any],
    passes: list[list[str]],
    bundle_key: str,
) -> dict[str, object]:
    """Render (or reuse cached) RGB orthophoto COGs for every `[scene_id, pass_date]` in `passes`,
    zip them, and store the archive at `bundle_key`. Emits PROGRESS `{done, total}` after each pass
    so the workspace can show a meter through the generic job-status endpoint. Returns the key
    and counts. Raises when storage is absent or no pass rendered, so the job settles to `error`."""
    settings = get_settings()
    if cog_store_from_settings(settings) is None:
        log.warning("bundle_aoi_orthophotos.no_store", key=bundle_key)
        raise RuntimeError("object storage is not available")
    store = S3CogStore(settings)

    geom_hash = canonical_geometry_hash(geometry)
    total = len(passes)

    async def _gather() -> tuple[list[tuple[str, bytes]], int]:
        adapter = get_access_adapter(settings)
        rendered: list[tuple[str, bytes]] = []
        skipped = 0
        for i, item in enumerate(passes):
            scene_id, pass_date = item[0], item[1]
            key = aoi_rgb_cog_key(scene_id, geom_hash)
            try:
                if store.exists(key):
                    cog = store.get_bytes(key)
                else:
                    cog = await _render_clipped_rgb_cog(adapter, scene_id, geometry, pass_date)
                    store.put(key, cog, content_type="image/tiff")
                rendered.append((f"rgb_{scene_id}_{pass_date}.tif", cog))
            except Exception:
                skipped += 1
                log.warning(
                    "bundle_aoi_orthophotos.pass_failed",
                    scene_id=scene_id,
                    pass_date=pass_date,
                    exc_info=True,
                )
            self.update_state(state="PROGRESS", meta={"done": i + 1, "total": total})
        return rendered, skipped

    rendered, skipped = asyncio.run(_gather())
    if not rendered:
        raise RuntimeError("no orthophotos could be rendered for this bundle")

    buffer = BytesIO()
    # ZIP_STORED: the COGs are already DEFLATE/LZW-compressed internally, so re-compressing the
    # archive only burns CPU for no size win.
    with ZipFile(buffer, "w", ZIP_STORED) as archive:
        for name, cog in rendered:
            archive.writestr(name, cog)
    zip_bytes = buffer.getvalue()

    store.put(bundle_key, zip_bytes, content_type="application/zip")
    log.info(
        "bundle_aoi_orthophotos.written",
        key=bundle_key,
        passes=len(rendered),
        skipped=skipped,
        bytes=len(zip_bytes),
    )
    return {"key": bundle_key, "passes": len(rendered), "skipped": skipped, "bytes": len(zip_bytes)}
