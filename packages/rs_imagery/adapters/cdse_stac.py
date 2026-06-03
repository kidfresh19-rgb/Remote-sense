"""CDSE STAC search client for the windowed_cog adapter. Discovers Sentinel-2 L2A scenes over an
AOI and time range and normalises each STAC item to a `SceneRef`, keeping the per-scene asset map
so `fetch`/`metadata` can resolve hrefs without a second round trip.

The standard STAC fields (id, datetime, geometry, `eo:cloud_cover`, `assets[].href`) are stable
across STAC APIs, so the parsing here is provider-agnostic. The Sentinel-2-specific asset key
naming and the CDSE query-filter syntax are marked `# ⚑ CONFIRM` and are the only details to verify
against the live catalogue. Zero-network testable: inject an `httpx.AsyncClient` backed by
`httpx.MockTransport`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from rs_core.config import Settings
from rs_core.logging import get_logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from rs_imagery.auth import CdseOAuth2Client
from rs_imagery.types import AOI, SceneRef, TimeRange

log = get_logger("rs_imagery.cdse_stac")

PROVIDER = "cdse"


def _is_transient(exc: BaseException) -> bool:
    """Retry on network faults and CDSE throttling/5xx; never on 4xx (mirrors the OAuth2 client)."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


def _iso_utc(dt: datetime) -> str:
    """An RFC3339 UTC timestamp for the STAC `datetime` range. Naive datetimes are assumed UTC
    (the TimeRange contract carries UTC)."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class StacItem:
    """A parsed STAC item the adapter keeps so `fetch`/`metadata` can resolve asset hrefs without
    re-querying. STAC geometry is WGS84 (EPSG:4326) by specification."""

    scene_id: str
    sensing_datetime: datetime
    geometry: dict[str, Any]
    crs: str
    cloud_cover: float | None
    assets: dict[str, dict[str, Any]]

    def to_scene_ref(self) -> SceneRef:
        return SceneRef(
            scene_id=self.scene_id,
            provider=PROVIDER,
            sensing_datetime=self.sensing_datetime,
            footprint=self.geometry,
            crs=self.crs,
            scene_cloud_pct=self.cloud_cover,
        )


def parse_item(feature: dict[str, Any]) -> StacItem | None:
    """Normalise one STAC feature to a `StacItem`, or None if it lacks an id or a usable
    timestamp (a malformed feature is skipped, not fatal)."""
    scene_id = feature.get("id")
    props = feature.get("properties") or {}
    dt_raw = props.get("datetime") or props.get("start_datetime")
    if not scene_id or not isinstance(dt_raw, str):
        return None
    try:
        sensing = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if sensing.tzinfo is None:
        sensing = sensing.replace(tzinfo=UTC)
    cloud = props.get("eo:cloud_cover")
    return StacItem(
        scene_id=str(scene_id),
        sensing_datetime=sensing.astimezone(UTC),
        geometry=feature.get("geometry") or {},
        crs="EPSG:4326",
        cloud_cover=float(cloud) if cloud is not None else None,
        assets=feature.get("assets") or {},
    )


def resolve_band_asset(assets: dict[str, dict[str, Any]], band: str, resolution_m: float) -> str:
    """The href of the raster asset for `band` at (or nearest) `resolution_m`.

    # ⚑ CONFIRM: CDSE S2 L2A asset key naming. Tries the exact band key, a resolution-suffixed key,
    then any asset whose key or href carries the band token. Confirm the real key scheme against the
    live catalogue and adjust the candidate list if needed."""
    res = int(resolution_m)
    candidates = (
        band,
        f"{band}_{res}m",
        f"{band}_{res:02d}m",
        band.lower(),
        f"{band.lower()}_{res}m",
    )
    for key in candidates:
        asset = assets.get(key)
        if asset and asset.get("href"):
            return str(asset["href"])
    token = band.upper()
    for key, asset in assets.items():
        href = str(asset.get("href", ""))
        if token in key.upper() or token in href.upper():
            if href:
                return href
    raise KeyError(f"no asset for band {band!r} at {res} m among {sorted(assets)}")


def resolve_metadata_href(assets: dict[str, dict[str, Any]]) -> str:
    """The product-metadata (`MTD_MSIL2A.xml`) href.

    # ⚑ CONFIRM: the CDSE asset key for product metadata. Falls back to any href ending in
    `MTD_MSIL2A.xml`."""
    for key in ("product_metadata", "metadata", "MTD_MSIL2A"):
        asset = assets.get(key)
        if asset and asset.get("href"):
            return str(asset["href"])
    for asset in assets.values():
        href = str(asset.get("href", ""))
        if href.upper().endswith("MTD_MSIL2A.XML"):
            return href
    raise KeyError("no product-metadata asset (MTD_MSIL2A.xml) in the STAC item")


class CdseStacClient:
    """Searches the CDSE STAC catalogue for Sentinel-2 L2A scenes. Resilience (retry/backoff on
    429/5xx) is centralised here, in the access layer (invariant 1)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        oauth: CdseOAuth2Client | None = None,
        max_attempts: int = 4,
        wait_min: float = 1.0,
        wait_max: float = 20.0,
        wait_multiplier: float = 1.0,
    ) -> None:
        if not settings.cdse_stac_url:
            raise ValueError("RS_CDSE_STAC_URL is not configured; cannot search CDSE.")
        self._search_url = settings.cdse_stac_url.rstrip("/") + "/search"
        self._collection = settings.cdse_stac_collection
        self._client = client or httpx.AsyncClient(timeout=60.0)
        self._owns_client = client is None
        self._oauth = oauth
        self._retrying = AsyncRetrying(
            retry=retry_if_exception(_is_transient),
            wait=wait_exponential(multiplier=wait_multiplier, min=wait_min, max=wait_max),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        )

    async def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._oauth is not None:
            headers.update(await self._oauth.authorization_header())
        return headers

    def _search_body(
        self, aoi: AOI, time_range: TimeRange, max_scene_cloud_pct: float | None, limit: int
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "collections": [self._collection],
            "datetime": f"{_iso_utc(time_range.start)}/{_iso_utc(time_range.end)}",
            "intersects": aoi.geometry,
            "limit": limit,
        }
        # ⚑ CONFIRM: CDSE STAC query-extension syntax. Restrict to L2A; add the cloud-cover filter
        # only as a coarse pre-filter (per-AOI SCL masking is authoritative, invariant 3).
        query: dict[str, Any] = {"productType": {"eq": "S2MSI2A"}}
        if max_scene_cloud_pct is not None:
            query["eo:cloud_cover"] = {"lte": max_scene_cloud_pct}
        body["query"] = query
        return body

    async def _post_search(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(self._search_url, json=body, headers=await self._headers())
        resp.raise_for_status()
        return resp.json()

    async def search_items(
        self,
        aoi: AOI,
        time_range: TimeRange,
        *,
        max_scene_cloud_pct: float | None = None,
        limit: int = 100,
    ) -> list[StacItem]:
        """The scenes covering the AOI in range, sorted chronologically (oldest first) so the
        pipeline plans backfill in pass order."""
        body = self._search_body(aoi, time_range, max_scene_cloud_pct, limit)
        payload = await self._retrying(self._post_search, body)
        items = [item for f in payload.get("features", []) if (item := parse_item(f)) is not None]
        items.sort(key=lambda i: i.sensing_datetime)
        log.info("cdse.stac.search", returned=len(items), collection=self._collection)
        return items

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
