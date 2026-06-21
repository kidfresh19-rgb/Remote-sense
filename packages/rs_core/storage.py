"""Object-store keys + the COG store for index previews (D1/D7). The key scheme and the GDAL/S3
env are pure and host-testable; the S3/MinIO client needs `boto3` (`storage` extra), and the tiler
reads those objects through GDAL's `/vsis3/` driver (`geo` extra). Both run in-container."""

from __future__ import annotations

from typing import Protocol

from rs_core.config import Settings


def cog_key(*, field_id: object, scene_id: str, index: str, geometry_version: int) -> str:
    """The object key an index COG is stored under. Tied to geometry_version so a boundary change
    (DI-5) writes a distinct object and the tiler never serves a stale render."""
    return f"cog/v{geometry_version}/{field_id}/{scene_id}/{index}.tif"


def aoi_tmp_cog_key(job_id: str, pass_date: str, index: str) -> str:
    """Temporary index COG emitted during an AOI Studio job. A 24-hour lifecycle rule on
    `aoi_tmp/` expires these automatically; the pipeline never persists them (invariant 7)."""
    return f"aoi_tmp/{job_id}/{pass_date}/{index}.tif"


def aoi_preview_key(scene_id: str, geometry_hash: str) -> str:
    """Cached natural-colour JPEG for a custom AOI scene. A 7-day lifecycle rule on
    `aoi_preview/` expires these automatically."""
    return f"aoi_preview/{scene_id}/{geometry_hash}.jpg"


def vsis3_uri(bucket: str, key: str) -> str:
    """The GDAL `/vsis3/` path the tiler opens for a stored COG (read path, configured by
    `gdal_s3_env`)."""
    return f"/vsis3/{bucket}/{key}"


def gdal_s3_env(settings: Settings) -> dict[str, str]:
    """GDAL `/vsis3/` configuration for the MinIO/S3 endpoint, to wrap the tiler's COG reads in a
    `rasterio.Env`. Path-style addressing (MinIO), credentials from settings. `render_tile` moves
    the credential keys into the process environment before building the Env, because rasterio 1.4
    refuses AWS credentials as Env options (GDAL still reads them from the environment)."""
    return {
        "AWS_S3_ENDPOINT": settings.minio_endpoint,
        "AWS_ACCESS_KEY_ID": settings.minio_access_key,
        "AWS_SECRET_ACCESS_KEY": settings.minio_secret_key,
        "AWS_HTTPS": "YES" if settings.minio_secure else "NO",
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    }


class CogStore(Protocol):
    """Where derived index COGs live. The pipeline puts, the retention job deletes (S4.3); the
    tiler reads (via GDAL, out of band)."""

    def put(self, key: str, data: bytes) -> None: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


class S3CogStore:
    """A MinIO/S3-backed COG store via boto3 (the `storage` extra). Construction imports boto3, so
    a host without it fails fast and `cog_store_from_settings` falls back to no emission."""

    def __init__(self, settings: Settings) -> None:
        import boto3  # lazy: the `storage` extra, installed in-container

        scheme = "https" if settings.minio_secure else "http"
        self._client = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{settings.minio_endpoint}",
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
        )
        self._bucket = settings.minio_bucket

    def put(self, key: str, data: bytes, *, content_type: str = "image/tiff") -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception:
            return False
        return True

    def delete(self, key: str) -> None:
        # S3/MinIO semantics: deleting an absent key succeeds, which is what makes the
        # retention job idempotent across partial runs.
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def get_bytes(self, key: str) -> bytes:
        """Download an object for proxying to the browser (small COGs and JPEG previews only)."""
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()  # type: ignore[return-value]

    def presigned_url(
        self,
        key: str,
        *,
        filename: str | None = None,
        expires: int = 900,
    ) -> str:
        """A pre-signed GET URL valid for `expires` seconds (15 min default). Pass `filename`
        to set a Content-Disposition: attachment header so the browser saves the file."""
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self._client.generate_presigned_url(  # type: ignore[return-value]
            "get_object", Params=params, ExpiresIn=expires
        )


def cog_store_from_settings(settings: Settings) -> CogStore | None:
    """The active COG store, or None when boto3 (the `storage` extra) is absent - in which case the
    pipeline runs without emitting previews (on a host, or before the stack is provisioned)."""
    try:
        return S3CogStore(settings)
    except ImportError:
        return None
