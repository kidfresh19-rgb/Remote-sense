"""MockAdapter: deterministic synthetic imagery with no network and no credentials. It
makes the entire platform testable against the AccessPort contract from day one (the old
DEV_MODE, now a first-class adapter). Reflectance values are plausible (NIR > Red so NDVI
is positive over vegetation) and reproducible from the scene id."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np

from rs_imagery.port import AccessPort
from rs_imagery.types import (
    _BLANK_PNG,
    AOI,
    BandStack,
    NormalizedResult,
    PreviewTile,
    ProcessingMode,
    Provenance,
    SceneMetadata,
    SceneRef,
    TimeRange,
)

_PROVIDER = "mock"
# Sentinel-2 Processing Baseline 04.00 radiometric offset. The mock mirrors reality so
# downstream reflectance-conversion code is exercised exactly as it will be in production.
_QUANTIFICATION_VALUE = 10000.0
_BOA_ADD_OFFSET = -1000.0
_TILE_SHAPE = (64, 64)


def _seed_for(scene_id: str) -> int:
    return int.from_bytes(hashlib.sha256(scene_id.encode()).digest()[:8], "big")


class MockAdapter(AccessPort):
    def __init__(self, *, revisit_days: int = 5, scenes_per_search: int = 8) -> None:
        self._revisit_days = revisit_days
        self._scenes_per_search = scenes_per_search

    async def search(
        self,
        aoi: AOI,
        time_range: TimeRange,
        *,
        max_scene_cloud_pct: float | None = None,
    ) -> list[SceneRef]:
        scenes: list[SceneRef] = []
        cursor = time_range.start
        i = 0
        while cursor <= time_range.end and i < self._scenes_per_search:
            scene_id = f"S2_MOCK_{cursor:%Y%m%dT%H%M%S}"
            rng = np.random.default_rng(_seed_for(scene_id))
            cloud = float(rng.uniform(0, 80))
            if max_scene_cloud_pct is None or cloud <= max_scene_cloud_pct:
                scenes.append(
                    SceneRef(
                        scene_id=scene_id,
                        provider=_PROVIDER,
                        sensing_datetime=cursor,
                        footprint=aoi.geometry,
                        crs=aoi.crs,
                        scene_cloud_pct=cloud,
                    )
                )
            cursor += timedelta(days=self._revisit_days)
            i += 1
        return scenes

    async def metadata(self, scene_id: str) -> SceneMetadata:
        bands = ["B02", "B03", "B04", "B05", "B08", "B11"]
        return SceneMetadata(
            scene_id=scene_id,
            quantification_value=_QUANTIFICATION_VALUE,
            boa_add_offset={b: _BOA_ADD_OFFSET for b in bands},
            processing_baseline="04.00",
            scene_cloud_pct=None,
        )

    async def fetch(
        self,
        scene_ref: SceneRef,
        aoi: AOI,
        bands: Sequence[str],
        *,
        resolution_m: float | None = None,
    ) -> NormalizedResult:
        rng = np.random.default_rng(_seed_for(scene_ref.scene_id))
        h, w = _TILE_SHAPE
        # Build plausible reflectance (0..1). Vegetation: high NIR, low Red.
        base_nir = rng.uniform(0.30, 0.55, size=(h, w))
        base_red = rng.uniform(0.04, 0.12, size=(h, w))
        templates: dict[str, np.ndarray] = {
            "B02": rng.uniform(0.02, 0.08, size=(h, w)),
            "B03": rng.uniform(0.04, 0.10, size=(h, w)),
            "B04": base_red,
            "B05": base_nir * rng.uniform(0.55, 0.75, size=(h, w)),
            "B08": base_nir,
            "B11": rng.uniform(0.10, 0.25, size=(h, w)),
        }
        out: dict[str, np.ndarray] = {}
        for b in bands:
            arr = templates.get(b, rng.uniform(0.05, 0.30, size=(h, w))).astype("float32")
            # Inject a few NoData pixels (np.nan), never 0, to exercise masking paths.
            mask = rng.random((h, w)) < 0.02
            arr[mask] = np.nan
            out[b] = arr

        resolution = resolution_m if resolution_m is not None else 10.0
        clear = float(np.mean(~np.isnan(next(iter(out.values())))))
        return NormalizedResult(
            scene_id=scene_ref.scene_id,
            aoi=aoi,
            data=BandStack(
                bands=out,
                crs=aoi.crs,
                transform=(resolution, 0.0, 0.0, 0.0, -resolution, 0.0),
                resolution_m=resolution,
            ),
            clear_fraction=clear,
            provenance=Provenance(
                provider=_PROVIDER,
                provider_scene_id=scene_ref.scene_id,
                processing_mode=ProcessingMode.MOCK,
                accessed_at=datetime.now(UTC),
            ),
        )

    async def preview(
        self,
        aoi: AOI,
        index: str,
        scene_ref: SceneRef,
        *,
        colormap: str | None = None,
    ) -> PreviewTile:
        return PreviewTile(
            png=_BLANK_PNG,
            index=index,
            vmin=-1.0,
            vmax=1.0,
            colormap=colormap or "RdYlGn",
            provenance=Provenance(
                provider=_PROVIDER,
                provider_scene_id=scene_ref.scene_id,
                processing_mode=ProcessingMode.MOCK,
                accessed_at=datetime.now(UTC),
            ),
        )
