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


def vsis3_uri(bucket: str, key: str) -> str:
    """The GDAL `/vsis3/` path the tiler opens for a stored COG (read path, configured by
    `gdal_s3_env`)."""
    return f"/vsis3/{bucket}/{key}"


def gdal_s3_env(settings: Settings) -> dict[str, str]:
    """GDAL `/vsis3/` configuration for the MinIO/S3 endpoint, to wrap the tiler's COG reads in a
    `rasterio.Env`. Path-style addressing (MinIO), credentials from settings."""
    return {
        "AWS_S3_ENDPOINT": settings.minio_endpoint,
        "AWS_ACCESS_KEY_ID": settings.minio_access_key,
        "AWS_SECRET_ACCESS_KEY": settings.minio_secret_key,
        "AWS_HTTPS": "YES" if settings.minio_secure else "NO",
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    }


class CogStore(Protocol):
    """Where derived index COGs live. The pipeline puts; the tiler reads (via GDAL, out of band)."""

    def put(self, key: str, data: bytes) -> None: ...

    def exists(self, key: str) -> bool: ...


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

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType="image/tiff")

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception:
            return False
        return True


def cog_store_from_settings(settings: Settings) -> CogStore | None:
    """The active COG store, or None when boto3 (the `storage` extra) is absent - in which case the
    pipeline runs without emitting previews (on a host, or before the stack is provisioned)."""
    try:
        return S3CogStore(settings)
    except ImportError:
        return None
