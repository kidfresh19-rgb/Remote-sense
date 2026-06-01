"""Object-store key scheme + GDAL/S3 env for index COGs - pure, no client (boto3 / rasterio)."""

from __future__ import annotations

import uuid

from rs_core import cog_key, gdal_s3_env, vsis3_uri
from rs_core.config import Settings


def test_cog_key_is_versioned_and_scoped() -> None:
    fid = uuid.UUID("00000000-0000-0000-0000-0000000000ab")
    key = cog_key(field_id=fid, scene_id="S2_X", index="ndvi", geometry_version=3)
    assert key == f"cog/v3/{fid}/S2_X/ndvi.tif"


def test_vsis3_uri_is_gdal_path() -> None:
    uri = vsis3_uri("remote-sense", "cog/v1/f/s/ndvi.tif")
    assert uri == "/vsis3/remote-sense/cog/v1/f/s/ndvi.tif"


def test_gdal_s3_env_is_path_style_for_minio() -> None:
    env = gdal_s3_env(Settings(minio_endpoint="minio:9000", minio_secure=False))
    assert env["AWS_S3_ENDPOINT"] == "minio:9000"
    assert env["AWS_HTTPS"] == "NO"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"
