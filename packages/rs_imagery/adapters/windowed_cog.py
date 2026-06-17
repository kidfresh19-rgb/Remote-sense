"""The `windowed_cog` adapter: the real `AccessPort` for the stored backfill pipeline (PLAN §4),
where the engine owns the reflectance math and offset correctness must be guaranteed.

It discovers scenes through `CdseStacClient`, reads each band's AOI window through a `WindowSource`
seam, applies the per-scene reflectance offset with the one canonical `stack_to_reflectance`, masks
per-AOI on the SCL band (invariant 3), and returns the normalised `NormalizedResult`. All rasterio
and network access live behind `WindowSource`, so the adapter's logic is unit-testable with zero
network and no `geo` extra by injecting a fake source. See ADR 0002."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import numpy as np
from rs_analysis.bands import coarsest_resolution_m
from rs_analysis.reflectance import stack_to_reflectance
from rs_analysis.scl import clear_fraction, clear_mask
from rs_core.config import Settings, get_settings
from rs_core.logging import get_logger

from rs_imagery.adapters.cdse_metadata import parse_scene_metadata
from rs_imagery.adapters.cdse_stac import (
    CdseStacClient,
    StacItem,
    resolve_band_asset,
    resolve_metadata_href,
)
from rs_imagery.auth import CdseOAuth2Client
from rs_imagery.port import AccessPort
from rs_imagery.resilience import CircuitBreaker, sync_bucket_from_settings
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

log = get_logger("rs_imagery.windowed_cog")

_SCL_BAND = "SCL"
_PROVIDER = "cdse"


@dataclass(frozen=True)
class ReadWindow:
    """A windowed read of one band over the AOI: the raw DN array plus its georeferencing. The
    array is float so NoData survives as-is (DN == 0 is masked later, invariant 2)."""

    array: np.ndarray
    transform: tuple[float, float, float, float, float, float]
    crs: str


@runtime_checkable
class WindowSource(Protocol):
    """The only seam that touches rasterio and the network. The default reads CDSE COGs over GDAL
    `/vsis3/`; tests inject a fake returning synthetic arrays."""

    def read_window(
        self, href: str, *, aoi: AOI, resolution_m: float, resampling: str = "bilinear"
    ) -> ReadWindow:
        """Read `href` over the AOI bounding box, resampled to `resolution_m` pixels."""
        ...

    def aoi_mask(self, aoi: AOI, *, like: ReadWindow) -> np.ndarray:
        """A boolean mask on `like`'s grid: True for pixels inside the AOI polygon."""
        ...

    def read_bytes(self, href: str) -> bytes:
        """The raw bytes of a file asset (the product metadata XML)."""
        ...


class WindowedCogAdapter(AccessPort):
    """Reads reflectance-corrected, SCL-masked AOI windows from CDSE Sentinel-2 L2A products.

    Construction never touches the network or credentials; the STAC client and the rasterio window
    source are built lazily on first use (so the registry can return the adapter and its logic can
    be exercised offline). `search` caches the discovered STAC items so `metadata`/`fetch` resolve
    asset hrefs without re-querying; call `search` before `fetch` (the collection pipeline does)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        stac_client: CdseStacClient | None = None,
        window_source: WindowSource | None = None,
        oauth: CdseOAuth2Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._stac_client = stac_client
        self._window_source = window_source
        self._oauth = oauth
        self._items: dict[str, StacItem] = {}

    # -- lazy construction of the network-facing collaborators ------------------------------------

    def _stac(self) -> CdseStacClient:
        if self._stac_client is None:
            self._stac_client = CdseStacClient(self._settings, oauth=self._oauth)
        return self._stac_client

    def _source(self) -> WindowSource:
        if self._window_source is None:
            self._window_source = RasterioWindowSource(self._settings)
        return self._window_source

    def _item(self, scene_id: str) -> StacItem:
        try:
            return self._items[scene_id]
        except KeyError as exc:
            raise LookupError(
                f"scene {scene_id!r} is not cached; call search() before metadata()/fetch() so the "
                "adapter can resolve its asset hrefs"
            ) from exc

    # -- AccessPort ------------------------------------------------------------------------------

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
        href = resolve_metadata_href(item.assets)
        # The XML read is a blocking CDSE/S3 call; run it off the event loop so concurrent passes
        # (ADR 0011) are not serialised by it. The per-task memo (read_bytes) dedupes repeats.
        xml_bytes = await asyncio.to_thread(self._source().read_bytes, href)
        return parse_scene_metadata(scene_id, xml_bytes, crs=item.crs)

    async def fetch(
        self,
        scene_ref: SceneRef,
        aoi: AOI,
        bands: Sequence[str],
        *,
        resolution_m: float | None = None,
    ) -> NormalizedResult:
        """Reflectance-corrected, per-AOI SCL-masked bands for one scene. Pixels outside the AOI
        polygon or not in a clear SCL class are set to NaN, so downstream statistics summarise only
        valid in-field observations; the clear fraction is the per-AOI SCL clear fraction."""
        requested = [b for b in bands if b != _SCL_BAND]
        if not requested:
            raise ValueError("fetch needs at least one reflectance band")
        item = self._item(scene_ref.scene_id)
        source = self._source()
        res = (
            float(resolution_m)
            if resolution_m is not None
            else float(coarsest_resolution_m(tuple(requested)))
        )

        # Read every reflectance band on the same grid; the first read is the reference grid.
        # Each blocking CDSE read runs off the event loop (asyncio.to_thread) so concurrent passes
        # (ADR 0011) overlap up to the quota ceiling; band reads stay sequential within one fetch
        # so the reference-grid shape check holds and the per-task band memo can dedupe repeats.
        dn: dict[str, np.ndarray] = {}
        reference: ReadWindow | None = None
        for band in requested:
            href = resolve_band_asset(item.assets, band, res)
            window = await asyncio.to_thread(
                source.read_window, href, aoi=aoi, resolution_m=res, resampling="bilinear"
            )
            if reference is None:
                reference = window
            elif window.array.shape != reference.array.shape:
                raise ValueError(
                    f"band {band} window {window.array.shape} does not match the reference grid "
                    f"{reference.array.shape}; all bands of a resolution group must align"
                )
            dn[band] = window.array
        assert reference is not None  # `requested` is non-empty

        # SCL on the same grid (nearest only: it is a class label, never interpolate it).
        scl_href = resolve_band_asset(item.assets, _SCL_BAND, res)
        scl = (
            await asyncio.to_thread(
                source.read_window, scl_href, aoi=aoi, resolution_m=res, resampling="nearest"
            )
        ).array

        meta = await self.metadata(scene_ref.scene_id)
        reflectance = stack_to_reflectance(
            dn, add_offset=meta.boa_add_offset, quantification=meta.quantification_value
        )

        inside = source.aoi_mask(aoi, like=reference)
        keep = clear_mask(scl) & inside
        masked = {band: np.where(keep, arr, np.nan) for band, arr in reflectance.items()}
        clear = clear_fraction(scl, inside)

        return NormalizedResult(
            scene_id=scene_ref.scene_id,
            aoi=aoi,
            data=BandStack(
                bands=masked,
                crs=reference.crs,
                transform=reference.transform,
                resolution_m=res,
            ),
            clear_fraction=clear,
            provenance=Provenance(
                provider=_PROVIDER,
                provider_scene_id=scene_ref.scene_id,
                processing_mode=ProcessingMode.WINDOWED_COG,
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
        """Previews and live tiles are served by the tiler (L5) from stored COGs, or by the
        `server_compute` adapter. windowed_cog owns the stored-pipeline read path only."""
        raise NotImplementedError(
            "windowed_cog serves the stored backfill pipeline; previews/live tiles come from the "
            "tiler (L5) over emitted COGs, or from the server_compute adapter"
        )


class _ReadMemo:
    """Per-task in-process memo for the windowed reads and the metadata XML of one analysis batch
    (ADR 0011). The COG objects are immutable, so within a task the same (band, bbox, resolution)
    window and the same metadata href can be served once and reused - interpolation re-reads its
    bracket scenes across several requested dates, and every fetch re-reads its scene metadata.

    Thread-safe: a guard lock protects the maps and a per-key lock collapses concurrent identical
    reads to a single underlying read, so the read storm stays at the quota ceiling. Scoped to the
    source instance, which the registry builds fresh per task, so it is dropped at task end
    (invariant 7: raw bands stay transient, never a persisted per-field store)."""

    def __init__(self) -> None:
        self._values: dict[Any, Any] = {}
        self._key_locks: dict[Any, threading.Lock] = {}
        self._guard = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: Any, compute: Callable[[], Any]) -> Any:
        """Return the cached value for `key`, or run `compute()` once and cache it. A read already
        in flight for the same key is awaited rather than duplicated."""
        with self._guard:
            if key in self._values:
                self.hits += 1
                return self._values[key]
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            with self._guard:
                if key in self._values:  # another thread filled it while we waited
                    self.hits += 1
                    return self._values[key]
                self.misses += 1
            value = compute()  # outside the guard so other keys are not blocked on this read
            with self._guard:
                self._values[key] = value
            return value


class RasterioWindowSource:
    """Default `WindowSource`: GDAL `/vsis3/` windowed reads against the CDSE `eodata` store. Needs
    the `geo` extra (rasterio) and runs in-container. Credentials follow the rasterio-1.4 pattern
    (creds in the process environment, endpoint/addressing as `rasterio.Env` options), the same
    handling the tiler uses."""

    def __init__(self, settings: Settings) -> None:
        if not settings.cdse_s3_endpoint:
            raise ValueError("RS_CDSE_S3_ENDPOINT is not configured; cannot read CDSE rasters.")
        self._settings = settings
        self._s3_client = None  # boto3 S3 client for the eodata store, built once on first use
        self._s3_lock = threading.Lock()  # guards the lazy build under concurrent reads
        # Quota governance (S4.5): every windowed/metadata read draws from the same cross-worker
        # CDSE budget as the STAC search; the breaker stops a melting eodata store from burning
        # each task's full retry loop.
        self._bucket = sync_bucket_from_settings(settings)
        self._breaker = CircuitBreaker()
        # Per-task band/metadata memo (ADR 0011): dedupes repeat reads within one analysis batch.
        self._memo = _ReadMemo()

    def cache_stats(self) -> dict[str, int]:
        """The per-task band/metadata memo's hit and miss counts (ADR 0011 instrumentation): a
        miss is one real CDSE read, so misses is the read count and hits is what was saved."""
        return {"hits": self._memo.hits, "misses": self._memo.misses}

    def _gdal_env(self) -> dict[str, str]:
        s = self._settings
        env = {
            "AWS_S3_ENDPOINT": s.cdse_s3_endpoint,
            "AWS_ACCESS_KEY_ID": s.cdse_s3_access_key,
            "AWS_SECRET_ACCESS_KEY": s.cdse_s3_secret_key,
            "AWS_REGION": s.cdse_s3_region,
            "AWS_VIRTUAL_HOSTING": "FALSE",
            "AWS_HTTPS": "YES",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        }
        # rasterio 1.4 refuses AWS credentials as Env options; GDAL reads them from the environment.
        for cred_key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            os.environ[cred_key] = env.pop(cred_key)
        return env

    @staticmethod
    def _to_vsis3(href: str) -> str:
        """Map an `s3://bucket/key` href to the GDAL `/vsis3/bucket/key` path rasterio reads."""
        if href.startswith("s3://"):
            return "/vsis3/" + href[len("s3://") :]
        return href

    def _make_retrying(self):
        """A sync retry for transient GDAL/network faults during windowed reads (R-3: CDSE
        throttling and transient S3 errors surface as RasterioIOError). Lazy because the exception
        type needs the geo extra."""
        from rasterio.errors import RasterioIOError
        from tenacity import (
            Retrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        return Retrying(
            retry=retry_if_exception_type(RasterioIOError),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            stop=stop_after_attempt(4),
            reraise=True,
        )

    def read_window(
        self, href: str, *, aoi: AOI, resolution_m: float, resampling: str = "bilinear"
    ) -> ReadWindow:
        # A memo hit returns the cached window without spending a quota token or touching CDSE.
        return self._memo.get_or_compute(
            self._window_key(href, aoi, resolution_m, resampling),
            lambda: self._read_window_quota_guarded(
                href, aoi=aoi, resolution_m=resolution_m, resampling=resampling
            ),
        )

    @staticmethod
    def _window_key(href: str, aoi: AOI, resolution_m: float, resampling: str) -> tuple[Any, ...]:
        """The band-memo key (ADR 0011): the read is bbox-scoped (the AOI polygon mask is applied
        later in fetch), so two AOIs sharing a bbox legitimately share one read - the polygon is
        deliberately excluded. The bbox is the exact AOI bounds, rounded only to kill float noise,
        i.e. finer than any read grid, so the key can never be coarser than the window derivation:
        a near-miss costs at worst a reread, never a wrong array."""
        from shapely.geometry import shape

        minx, miny, maxx, maxy = shape(aoi.geometry).bounds
        return (
            href,
            resampling,
            round(float(resolution_m), 6),
            aoi.crs,
            round(minx, 9),
            round(miny, 9),
            round(maxx, 9),
            round(maxy, 9),
        )

    def _read_window_quota_guarded(
        self, href: str, *, aoi: AOI, resolution_m: float, resampling: str = "bilinear"
    ) -> ReadWindow:
        if self._bucket is not None:
            self._bucket.acquire()
        return self._breaker.call_sync(
            self._make_retrying(),
            self._read_window_once,
            href,
            aoi=aoi,
            resolution_m=resolution_m,
            resampling=resampling,
        )

    def _read_window_once(
        self, href: str, *, aoi: AOI, resolution_m: float, resampling: str = "bilinear"
    ) -> ReadWindow:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
        from shapely.geometry import shape

        method = Resampling.nearest if resampling == "nearest" else Resampling.bilinear
        minx, miny, maxx, maxy = shape(aoi.geometry).bounds
        with rasterio.Env(**self._gdal_env()):
            with rasterio.open(self._to_vsis3(href)) as src:
                left, bottom, right, top = transform_bounds(
                    aoi.crs, src.crs, minx, miny, maxx, maxy, densify_pts=21
                )
                window = from_bounds(left, bottom, right, top, transform=src.transform)
                out_w = max(1, round((right - left) / resolution_m))
                out_h = max(1, round((top - bottom) / resolution_m))
                array = src.read(
                    1, window=window, out_shape=(out_h, out_w), resampling=method, boundless=True
                ).astype("float64")
                win_transform = src.window_transform(window)
                scale_x = window.width / out_w if out_w else 1.0
                scale_y = window.height / out_h if out_h else 1.0
                out_transform = win_transform * win_transform.scale(scale_x, scale_y)
                return ReadWindow(
                    array=array,
                    transform=(
                        out_transform.a,
                        out_transform.b,
                        out_transform.c,
                        out_transform.d,
                        out_transform.e,
                        out_transform.f,
                    ),
                    crs=str(src.crs),
                )

    def aoi_mask(self, aoi: AOI, *, like: ReadWindow) -> np.ndarray:
        from rasterio.features import geometry_mask
        from rasterio.transform import Affine
        from rasterio.warp import transform_geom

        crs = like.crs
        geom = transform_geom(aoi.crs, crs, aoi.geometry) if aoi.crs != crs else aoi.geometry
        # geometry_mask returns True OUTSIDE the geometry; invert so True == inside the AOI.
        outside = geometry_mask(
            [geom],
            out_shape=like.array.shape,
            transform=Affine(*like.transform),
            invert=False,
        )
        return ~outside

    def read_bytes(self, href: str) -> bytes:
        """The raw bytes of a file asset (the product metadata XML). CDSE serves these as `s3://`
        objects in the eodata store, read with boto3 whose adaptive retry absorbs 429 throttling and
        transient S3 faults with backoff (R-3, the metadata-read analogue of the windowed-read
        retry). A plain http(s) href falls back to a retrying GET. boto3 is the `storage` extra,
        installed alongside `geo` on the COG-emitting worker where this adapter runs. A memo hit
        returns the cached XML without spending a quota token or touching CDSE."""
        return self._memo.get_or_compute(
            ("__bytes__", href), lambda: self._read_bytes_quota_guarded(href)
        )

    def _read_bytes_quota_guarded(self, href: str) -> bytes:
        if self._bucket is not None:
            self._bucket.acquire()
        if href.startswith("s3://"):
            return self._breaker.call_sync(self._read_s3_bytes, href)
        return self._breaker.call_sync(self._read_http_bytes, href)

    def _s3(self):
        """The boto3 S3 client for the CDSE eodata store, built once. Caching it preserves
        botocore's adaptive-retry token state across reads (R-3) and avoids per-read client setup,
        the way rs_core.storage.S3CogStore caches its client."""
        if self._s3_client is None:
            with self._s3_lock:  # double-checked: build once even under concurrent reads
                if self._s3_client is None:
                    self._s3_client = self._build_s3_client()
        return self._s3_client

    def _build_s3_client(self):  # noqa: ANN202 - boto3 client type needs the storage extra
        import boto3
        from botocore.config import Config

        s = self._settings
        return boto3.client(
            "s3",
            endpoint_url=f"https://{s.cdse_s3_endpoint}",
            aws_access_key_id=s.cdse_s3_access_key,
            aws_secret_access_key=s.cdse_s3_secret_key,
            region_name=s.cdse_s3_region,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 4, "mode": "adaptive"},
            ),
        )

    def _read_s3_bytes(self, href: str) -> bytes:
        bucket, _, key = href[len("s3://") :].partition("/")
        return self._s3().get_object(Bucket=bucket, Key=key)["Body"].read()

    def _read_http_bytes(self, href: str) -> bytes:
        import httpx
        from tenacity import (
            Retrying,
            retry_if_exception,
            stop_after_attempt,
            wait_exponential,
        )

        def _transient(exc: BaseException) -> bool:
            if isinstance(exc, httpx.TransportError):
                return True
            if isinstance(exc, httpx.HTTPStatusError):
                return exc.response.status_code in (429, 500, 502, 503, 504)
            return False

        def _get() -> bytes:
            resp = httpx.get(href, timeout=60.0)
            resp.raise_for_status()
            return resp.content

        retrying = Retrying(
            retry=retry_if_exception(_transient),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            stop=stop_after_attempt(4),
            reraise=True,
        )
        return retrying(_get)
