"""The AccessPort: the only seam between remote-sense and any satellite source. The
engine, pipeline and preview call only this interface. Resilience concerns (OAuth2 token
management, retry/backoff, circuit-breaking, quota) are centralized in adapters, never
scattered through callers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from rs_imagery.types import (
    AOI,
    NormalizedResult,
    PreviewTile,
    SceneMetadata,
    SceneRef,
    TimeRange,
)


class AccessPort(ABC):
    """Four operations: search, metadata, fetch, preview. Every adapter normalises its
    output to the shared types so downstream code is identical regardless of endpoint."""

    @abstractmethod
    async def search(
        self,
        aoi: AOI,
        time_range: TimeRange,
        *,
        max_scene_cloud_pct: float | None = None,
    ) -> list[SceneRef]:
        """Return scene references (metadata only, no pixels) covering the AOI in range."""

    @abstractmethod
    async def metadata(self, scene_id: str) -> SceneMetadata:
        """Return per-scene radiometric metadata (offset, quantification value, etc.)."""

    @abstractmethod
    async def fetch(
        self,
        scene_ref: SceneRef,
        aoi: AOI,
        bands: Sequence[str],
        *,
        resolution_m: float | None = None,
    ) -> NormalizedResult:
        """Return reflectance-corrected bands for the AOI, masked per-AOI, with a
        clear-pixel fraction and provenance. resolution_m of None means native."""

    @abstractmethod
    async def preview(
        self,
        aoi: AOI,
        index: str,
        scene_ref: SceneRef,
        *,
        colormap: str | None = None,
    ) -> PreviewTile:
        """Return a fast, coarse rendered index tile (from COG overviews) for the AOI."""
