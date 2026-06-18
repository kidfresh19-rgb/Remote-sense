"""The `server_compute` adapter: endpoint-side rendering for fast previews and live tiles (PLAN §4),
the second real `AccessPort` (ADR 0003).

`preview` renders a colorized index PNG server-side via the CDSE Process API. `fetch` requests the
reflectance bands (plus SCL and a dataMask) from the Process API and lets the engine compute the
index, so the index formula has one implementation and the two real adapters cannot drift (risk #5).
`search`/`metadata` reuse the shared CDSE STAC client and metadata parser. All HTTP lives behind an
injected `httpx.AsyncClient`, and GeoTIFF decoding behind a `RasterDecoder` seam, so the adapter's
logic is unit-tested with zero network and no `geo` extra. The Process API request body and the
evalscripts are CDSE-specific and marked `# ⚑ CONFIRM`."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import httpx
import numpy as np
from rs_analysis.bands import coarsest_resolution_m
from rs_analysis.colormaps import get_colormap
from rs_analysis.scl import clear_fraction, clear_mask
from rs_core.config import Settings, get_settings
from rs_core.logging import get_logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from rs_imagery.adapters.cdse_metadata import parse_scene_metadata
from rs_imagery.adapters.cdse_stac import CdseStacClient, StacItem, resolve_metadata_href
from rs_imagery.auth import CdseOAuth2Client
from rs_imagery.port import AccessPort
from rs_imagery.resilience import (
    AsyncTokenBucket,
    CircuitBreaker,
    async_bucket_from_settings,
)
from rs_imagery.types import (
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

log = get_logger("rs_imagery.server_compute")

_PROVIDER = "cdse"
_SCL_BAND = "SCL"
_DATA_MASK_BAND = "dataMask"


def _is_transient(exc: BaseException) -> bool:
    """Retry on network faults and CDSE throttling/5xx; never on 4xx."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


@dataclass(frozen=True)
class DecodedGrid:
    """A decoded Process API GeoTIFF: every output band by name, plus georeferencing."""

    bands: dict[str, np.ndarray]
    transform: tuple[float, float, float, float, float, float]
    crs: str


@runtime_checkable
class RasterDecoder(Protocol):
    """Decodes a multi-band GeoTIFF (the Process API response) into named bands. The default uses
    rasterio (the `geo` extra, in-container); tests inject a fake."""

    def decode(self, data: bytes, *, band_order: Sequence[str]) -> DecodedGrid: ...


def _bands_evalscript(bands: Sequence[str]) -> str:
    """An evalscript returning the requested reflectance bands plus SCL and dataMask as a
    multi-band FLOAT32 image, in `band_order`.

    # ⚑ CONFIRM: CDSE/Sentinel Hub evalscript syntax and band identifiers."""
    inputs = ", ".join(f'"{b}"' for b in bands)
    samples = ", ".join(f"s.{b}" for b in bands)
    n = len(bands) + 2
    return (
        "//VERSION=3\n"
        "function setup() {\n"
        f'  return {{ input: [{inputs}, "SCL", "dataMask"],\n'
        f'    output: {{ bands: {n}, sampleType: "FLOAT32" }} }};\n'
        "}\n"
        "function evaluatePixel(s) {\n"
        f"  return [{samples}, s.SCL, s.dataMask];\n"
        "}\n"
    )


def _index_evalscript(index: str) -> str:
    """An evalscript that computes `index` and returns a single-band FLOAT32 value (the tiler/PNG
    colormap is applied client-side from the locked display range).

    # ⚑ CONFIRM: CDSE/Sentinel Hub evalscript syntax and the per-index band math."""
    formulas = {
        "ndvi": "(s.B08 - s.B04) / (s.B08 + s.B04)",
        "evi2": "2.5 * (s.B08 - s.B04) / (s.B08 + 2.4 * s.B04 + 1.0)",
        "savi": "((s.B08 - s.B04) / (s.B08 + s.B04 + 0.5)) * 1.5",
        "ndre": "(s.B08 - s.B05) / (s.B08 + s.B05)",
        "ndmi": "(s.B08 - s.B11) / (s.B08 + s.B11)",
    }
    bands = {
        "ndvi": '"B04", "B08"',
        "evi2": '"B04", "B08"',
        "savi": '"B04", "B08"',
        "ndre": '"B05", "B08"',
        "ndmi": '"B08", "B11"',
    }
    name = index.lower()
    if name not in formulas:
        raise KeyError(f"no evalscript for index {index!r}")
    return (
        "//VERSION=3\n"
        "function setup() {\n"
        f'  return {{ input: [{bands[name]}, "dataMask"],\n'
        '    output: { bands: 1, sampleType: "FLOAT32" } };\n'
        "}\n"
        "function evaluatePixel(s) {\n"
        f"  return [{formulas[name]}];\n"
        "}\n"
    )


class ProcessClient:
    """POSTs render requests to the CDSE Process API. Resilience (429/5xx retry) is centralised here
    (invariant 1). Returns the raw response bytes (a GeoTIFF for `fetch`, a PNG for `preview`)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        oauth: CdseOAuth2Client | None = None,
        bucket: AsyncTokenBucket | None = None,
        breaker: CircuitBreaker | None = None,
        max_attempts: int = 4,
        wait_min: float = 1.0,
        wait_max: float = 20.0,
        wait_multiplier: float = 1.0,
    ) -> None:
        if not settings.cdse_process_url:
            raise ValueError("RS_CDSE_PROCESS_URL is not configured; cannot render server-side.")
        self._url = settings.cdse_process_url
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None
        self._oauth = oauth
        # Quota governance (S4.5): renders draw from the same cross-worker CDSE budget as the
        # STAC search and the windowed reads; the breaker refuses fast while CDSE is down.
        self._bucket = bucket if bucket is not None else async_bucket_from_settings(settings)
        self._breaker = breaker if breaker is not None else CircuitBreaker()
        self._retrying = AsyncRetrying(
            retry=retry_if_exception(_is_transient),
            wait=wait_exponential(multiplier=wait_multiplier, min=wait_min, max=wait_max),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        )

    async def render(self, body: dict, *, accept: str) -> bytes:
        async def _post() -> bytes:
            headers = {"Content-Type": "application/json", "Accept": accept}
            if self._oauth is not None:
                headers.update(await self._oauth.authorization_header())
            resp = await self._client.post(self._url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.content

        if self._bucket is not None:
            await self._bucket.acquire()
        return await self._breaker.call(self._retrying, _post)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ServerComputeAdapter(AccessPort):
    """Endpoint-side previews and reflectance fetch via the CDSE Process API. Constructs without
    network or credentials; collaborators are built lazily on first use, so the registry can return
    it and run its logic offline. `search` caches discovered items so `metadata`/`fetch` resolve
    scenes without re-querying."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        stac_client: CdseStacClient | None = None,
        process_client: ProcessClient | None = None,
        decoder: RasterDecoder | None = None,
        metadata_reader: Callable[[str], bytes] | None = None,
        oauth: CdseOAuth2Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._stac_client = stac_client
        self._process_client = process_client
        self._decoder = decoder
        self._metadata_reader = metadata_reader
        self._oauth = oauth
        self._items: dict[str, StacItem] = {}

    def _oauth_or_default(self) -> CdseOAuth2Client | None:
        """The CDSE OAuth client the Process API requires. Built lazily from settings when none was
        injected and credentials are configured: the registry constructs the adapter creds-free, so
        the client is created on first real use, not at construction. Stays None in offline/mock use
        where collaborators are injected and never reach the network."""
        if self._oauth is None and self._settings.cdse_token_url:
            self._oauth = CdseOAuth2Client(self._settings)
        return self._oauth

    def _stac(self) -> CdseStacClient:
        if self._stac_client is None:
            self._stac_client = CdseStacClient(self._settings, oauth=self._oauth_or_default())
        return self._stac_client

    def _process(self) -> ProcessClient:
        if self._process_client is None:
            self._process_client = ProcessClient(self._settings, oauth=self._oauth_or_default())
        return self._process_client

    def _decode(self) -> RasterDecoder:
        if self._decoder is None:
            self._decoder = RasterioRasterDecoder()
        return self._decoder

    def _read_metadata_bytes(self, href: str) -> bytes:
        if self._metadata_reader is not None:
            return self._metadata_reader(href)
        if href.lower().startswith(("http://", "https://")):
            resp = httpx.get(href, timeout=60.0)
            resp.raise_for_status()
            return resp.content
        raise NotImplementedError(
            f"server_compute needs an http(s) metadata href or an injected reader; got {href!r}"
        )

    def _item(self, scene_id: str) -> StacItem:
        try:
            return self._items[scene_id]
        except KeyError as exc:
            raise LookupError(
                f"scene {scene_id!r} is not cached; call search() before metadata()/fetch()"
            ) from exc

    @staticmethod
    def _output_dims(aoi: AOI, resolution_m: float) -> tuple[int, int]:
        """The output pixel grid for the AOI bbox at resolution_m. The bounds are WGS84, so the
        degree extent is converted to metres with a cos(lat) factor, then capped to the Process
        API's 2500 px limit per side. Verified against the live API 2026-06-04."""
        from shapely.geometry import shape

        minx, miny, maxx, maxy = shape(aoi.geometry).bounds
        mid = math.radians((miny + maxy) / 2.0)
        width = round((maxx - minx) * 111320.0 * math.cos(mid) / resolution_m)
        height = round((maxy - miny) * 111320.0 / resolution_m)
        return max(1, min(2500, width)), max(1, min(2500, height))

    def _bounds(
        self, aoi: AOI, datetime_iso: str, evalscript: str, *, fmt: str, resolution_m: float
    ) -> dict:
        """The Process API request body for the AOI and one scene's date, rendered on the AOI grid
        at resolution_m. Verified against the live CDSE Process API 2026-06-04 (ADR 0003)."""
        width, height = self._output_dims(aoi, resolution_m)
        return {
            "input": {
                "bounds": {"geometry": aoi.geometry},
                "data": [
                    {
                        "type": "sentinel-2-l2a",
                        "dataFilter": {
                            "timeRange": {"from": datetime_iso, "to": datetime_iso},
                            "mosaickingOrder": "mostRecent",
                        },
                    }
                ],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{"identifier": "default", "format": {"type": fmt}}],
            },
            "evalscript": evalscript,
        }

    async def search(
        self,
        aoi: AOI,
        time_range: TimeRange,
        *,
        max_scene_cloud_pct: float | None = None,
    ) -> list[SceneRef]:
        items = await self._stac().search_items(
            aoi, time_range, max_scene_cloud_pct=max_scene_cloud_pct
        )
        for item in items:
            self._items[item.scene_id] = item
        return [item.to_scene_ref() for item in items]

    async def metadata(self, scene_id: str) -> SceneMetadata:
        item = self._item(scene_id)
        xml_bytes = self._read_metadata_bytes(resolve_metadata_href(item.assets))
        return parse_scene_metadata(scene_id, xml_bytes, crs=item.crs)

    async def fetch(
        self,
        scene_ref: SceneRef,
        aoi: AOI,
        bands: Sequence[str],
        *,
        resolution_m: float | None = None,
    ) -> NormalizedResult:
        """Reflectance bands rendered by the Process API, masked per-AOI on SCL and the dataMask
        (invariant 3). The engine computes the index from these, identically to windowed_cog, so the
        two adapters cannot disagree (ADR 0003)."""
        requested = [b for b in bands if b not in (_SCL_BAND, _DATA_MASK_BAND)]
        if not requested:
            raise ValueError("fetch needs at least one reflectance band")
        item = self._item(scene_ref.scene_id)
        res = (
            float(resolution_m)
            if resolution_m is not None
            else float(coarsest_resolution_m(tuple(requested)))
        )
        sensing = item.sensing_datetime
        window = {
            "from": sensing.strftime("%Y-%m-%dT00:00:00Z"),
            "to": (sensing + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        }
        body = self._bounds(
            aoi, window["from"], _bands_evalscript(requested), fmt="image/tiff", resolution_m=res
        )
        body["input"]["data"][0]["dataFilter"]["timeRange"] = window
        tiff = await self._process().render(body, accept="image/tiff")

        order = [*requested, _SCL_BAND, _DATA_MASK_BAND]
        grid = self._decode().decode(tiff, band_order=order)
        decoded = dict(grid.bands)
        scl = decoded.pop(_SCL_BAND)
        data_mask = decoded.pop(_DATA_MASK_BAND)
        inside = data_mask > 0
        keep = clear_mask(scl) & inside
        masked = {band: np.where(keep, arr, np.nan) for band, arr in decoded.items()}
        clear = clear_fraction(scl, inside)

        return NormalizedResult(
            scene_id=scene_ref.scene_id,
            aoi=aoi,
            data=BandStack(bands=masked, crs=grid.crs, transform=grid.transform, resolution_m=res),
            clear_fraction=clear,
            provenance=Provenance(
                provider=_PROVIDER,
                provider_scene_id=scene_ref.scene_id,
                processing_mode=ProcessingMode.SERVER_COMPUTE,
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
        """A colorized index PNG rendered server-side for the map. The display range and colormap
        come from the locked per-index colormap so previews and stored tiles match."""
        cmap = get_colormap(index)  # validates the index; raises KeyError if unknown
        sensing = scene_ref.sensing_datetime
        body = self._bounds(
            aoi,
            sensing.strftime("%Y-%m-%dT00:00:00Z"),
            _index_evalscript(index),
            fmt="image/png",
            resolution_m=10.0,
        )
        body["input"]["data"][0]["dataFilter"]["timeRange"] = {
            "from": sensing.strftime("%Y-%m-%dT00:00:00Z"),
            "to": (sensing + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        }
        png = await self._process().render(body, accept="image/png")
        return PreviewTile(
            png=png,
            index=index,
            vmin=cmap.vmin,
            vmax=cmap.vmax,
            colormap=colormap or cmap.colormap,
            provenance=Provenance(
                provider=_PROVIDER,
                provider_scene_id=scene_ref.scene_id,
                processing_mode=ProcessingMode.SERVER_COMPUTE,
                accessed_at=datetime.now(UTC),
            ),
        )


class RasterioRasterDecoder:
    """Default `RasterDecoder`: decodes the Process API GeoTIFF with rasterio (the `geo` extra,
    in-container). Maps each band index to the requested `band_order`."""

    def decode(self, data: bytes, *, band_order: Sequence[str]) -> DecodedGrid:
        from rasterio.io import MemoryFile

        with MemoryFile(data) as mem, mem.open() as src:
            if src.count < len(band_order):
                raise ValueError(
                    f"Process API returned {src.count} bands, expected {len(band_order)}"
                )
            bands = {name: src.read(i + 1).astype("float64") for i, name in enumerate(band_order)}
            t = src.transform
            return DecodedGrid(
                bands=bands,
                transform=(t.a, t.b, t.c, t.d, t.e, t.f),
                crs=str(src.crs),
            )
