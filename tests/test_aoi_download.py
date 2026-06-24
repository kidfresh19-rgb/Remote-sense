"""Tests for the AOI Studio download endpoints (0020 / 0021).

No Celery broker, no MinIO, no DB required -- all external deps are monkeypatched.
"""

from __future__ import annotations

import pytest
from rs_core.rbac import Principal, Role
from starlette.testclient import TestClient

from services.api.auth import get_principal
from services.api.main import app

_PRESIGNED = "https://minio.internal/rs-cog/aoi_tmp/job1/2024-10-05/ndvi.tif?X-Amz-Signature=xyz"
_JOB = "job1"
_DATE = "2024-10-05"


def _analyst() -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="analyst", roles=frozenset({Role.ANALYST})
    )


def _teardown() -> None:
    app.dependency_overrides.clear()


# ── Pass download (0021) ───────────────────────────────────────────────────────


def test_aoi_pass_download_requires_auth() -> None:
    url = f"/analyse/aoi/jobs/{_JOB}/passes/{_DATE}/download?index=ndvi"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401


def test_aoi_pass_download_returns_302_presigned_url(monkeypatch) -> None:
    import rs_core.storage as _storage

    class _FakeStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return True

        def presigned_url(
            self, key: str, *, filename: str | None = None, expires: int = 900
        ) -> str:
            return _PRESIGNED

    monkeypatch.setattr(_storage, "S3CogStore", _FakeStore)
    _analyst()
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(
                f"/analyse/aoi/jobs/{_JOB}/passes/{_DATE}/download?index=ndvi",
                follow_redirects=False,
            )
    finally:
        _teardown()

    assert resp.status_code == 302
    assert resp.headers["location"] == _PRESIGNED


def test_aoi_pass_download_missing_cog_is_404(monkeypatch) -> None:
    import rs_core.storage as _storage

    class _MissingStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return False

    monkeypatch.setattr(_storage, "S3CogStore", _MissingStore)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.get(f"/analyse/aoi/jobs/{_JOB}/passes/{_DATE}/download?index=ndvi")
    finally:
        _teardown()

    assert resp.status_code == 404


def test_aoi_pass_download_missing_storage_is_503(monkeypatch) -> None:
    import rs_core.storage as _storage

    def _no_boto3(_settings):
        raise ImportError("boto3 not installed")

    monkeypatch.setattr(_storage, "S3CogStore", _no_boto3)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.get(f"/analyse/aoi/jobs/{_JOB}/passes/{_DATE}/download?index=ndvi")
    finally:
        _teardown()

    assert resp.status_code == 503


# ── Natural colour preview (0020) ──────────────────────────────────────────────

_NC_JPEG = b"\xff\xd8\xff\xe0FAKE_JPEG"
_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [[31.0, -17.8], [31.01, -17.8], [31.01, -17.81], [31.0, -17.81], [31.0, -17.8]]
    ],
}


def test_natural_color_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/analyse/aoi/natural-color",
            json={"scene_id": "S2A_TEST", "geometry": _GEOMETRY, "pass_date": "2024-10-05"},
        )
    assert resp.status_code == 401


def test_natural_color_cache_hit_returns_jpeg(monkeypatch) -> None:
    """When the preview already exists in MinIO, the endpoint proxies it directly."""
    import rs_core.storage as _storage

    class _HitStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return True

        def get_bytes(self, key: str) -> bytes:
            return _NC_JPEG

    monkeypatch.setattr(_storage, "S3CogStore", _HitStore)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/natural-color",
                json={"scene_id": "S2A_TEST", "geometry": _GEOMETRY, "pass_date": "2024-10-05"},
            )
    finally:
        _teardown()

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == _NC_JPEG


# ── Natural colour orthophoto GeoTIFF download (format=cog) ─────────────────────

_NC_COG = b"II*\x00FAKE_COG_BYTES"  # little-endian TIFF magic + filler


def test_natural_color_cog_cache_hit_returns_tiff(monkeypatch) -> None:
    """A cached RGB COG is proxied as an image/tiff download attachment."""
    import rs_core.storage as _storage

    class _HitStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return key.endswith(".tif")

        def get_bytes(self, key: str) -> bytes:
            return _NC_COG

    monkeypatch.setattr(_storage, "S3CogStore", _HitStore)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/natural-color",
                json={
                    "scene_id": "S2A_TEST",
                    "geometry": _GEOMETRY,
                    "pass_date": "2024-10-05",
                    "format": "cog",
                },
            )
    finally:
        _teardown()

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/tiff"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.tif"')
    assert resp.content == _NC_COG


def test_natural_color_cog_cache_miss_returns_202_job(monkeypatch) -> None:
    """A COG cache miss no longer blocks on the render (P5): it enqueues and returns 202 with a job
    id for the client to poll, so the request never holds a worker thread through a cold render."""
    import rs_core.storage as _storage

    import services.worker.tasks as _tasks

    class _MissStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return False

        def get_bytes(self, key: str) -> bytes:  # pragma: no cover - not reached on a miss
            return _NC_COG

    class _FakeResult:
        id = "render-job-123"

    class _FakeTask:
        @staticmethod
        def delay(*args: object, **kwargs: object) -> _FakeResult:
            return _FakeResult()

    monkeypatch.setattr(_storage, "S3CogStore", _MissStore)
    monkeypatch.setattr(_tasks, "render_natural_color_task", _FakeTask)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/natural-color",
                json={
                    "scene_id": "S2A_TEST",
                    "geometry": _GEOMETRY,
                    "pass_date": "2024-10-05",
                    "format": "cog",
                },
            )
    finally:
        _teardown()

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "render-job-123"
    assert body["state"] == "queued"


def test_render_rgb_cog_and_jpeg_from_synthetic_bands() -> None:
    """The split render helpers turn synthetic B02/B03/B04 reflectance into a 3-band COG and a
    JPEG rendered from it. Needs the geo extra (rasterio + rio_tiler); skips on a bare host."""
    pytest.importorskip("rasterio")
    pytest.importorskip("rio_tiler")

    import numpy as np

    from services.worker.tasks.analysis import _jpeg_from_cog, _render_rgb_cog

    bands = {
        "B04": np.full((16, 16), 0.15, dtype="float32"),
        "B03": np.full((16, 16), 0.10, dtype="float32"),
        "B02": np.full((16, 16), 0.05, dtype="float32"),
    }
    transform = (10.0, 0.0, 500000.0, 0.0, -10.0, 8030000.0)  # 10 m pixels, UTM-like origin
    cog = _render_rgb_cog(bands, transform, "EPSG:32735")
    assert cog[:2] in (b"II", b"MM")  # TIFF magic

    jpeg = _jpeg_from_cog(cog)
    assert jpeg[:3] == b"\xff\xd8\xff"  # JPEG magic


def test_render_rgb_cog_clips_outside_aoi_mask_to_nodata() -> None:
    """A mask passed to `_render_rgb_cog` writes outside-AOI pixels as NoData (NaN) in the decoded
    COG, while inside-AOI pixels keep their reflectance. This is the radiometric contract the
    polygon clip relies on. Needs the geo extra; skips on a bare host."""
    pytest.importorskip("rasterio")

    import numpy as np
    from rasterio.io import MemoryFile

    from services.worker.tasks.analysis import _render_rgb_cog

    bands = {b: np.full((8, 8), 0.12, dtype="float32") for b in ("B02", "B03", "B04")}
    mask = np.zeros((8, 8), dtype=bool)
    mask[2:6, 2:6] = True  # only the central 4x4 block is inside the AOI
    transform = (10.0, 0.0, 500000.0, 0.0, -10.0, 8030000.0)

    cog = _render_rgb_cog(bands, transform, "EPSG:32735", aoi_mask=mask)
    with MemoryFile(cog) as mem, mem.open() as src:
        red = src.read(1)

    assert np.isnan(red[0, 0])  # corner is outside the AOI -> NoData
    assert not np.isnan(red[3, 3])  # centre is inside the AOI -> kept
    assert red[3, 3] == pytest.approx(0.12)


def test_dn_zero_pixels_are_nodata_in_rgb_cog() -> None:
    """DN == 0 is NoData (invariant 2): pixels at DN zero must arrive as NaN in the render bands
    and survive as NoData in the written COG. This verifies the reflectance-conversion -> rgb_raster
    -> write_cog chain holds the contract end-to-end. Needs the geo extra; skips on a bare host."""
    pytest.importorskip("rasterio")

    import numpy as np
    from rasterio.io import MemoryFile
    from rs_analysis.reflectance import stack_to_reflectance

    from services.worker.tasks.analysis import (  # noqa: PLC0415
        _render_rgb_cog,
    )

    # Build DN arrays: most pixels at a healthy value, the top-left 2x2 block at DN == 0 (NoData).
    size = 8
    dn_b04 = np.full((size, size), 1500, dtype="float32")
    dn_b03 = np.full((size, size), 1000, dtype="float32")
    dn_b02 = np.full((size, size), 600, dtype="float32")
    dn_b04[:2, :2] = 0
    dn_b03[:2, :2] = 0
    dn_b02[:2, :2] = 0

    # Baseline 04.00 radiometric parameters (same as the mock adapter).
    reflectance = stack_to_reflectance(
        {"B04": dn_b04, "B03": dn_b03, "B02": dn_b02},
        add_offset=-1000.0,
        quantification=10000.0,
    )

    # Sanity: the conversion must have produced NaN for the zero-DN patch.
    assert np.isnan(reflectance["B04"][:2, :2]).all(), (
        "stack_to_reflectance did not NaN DN==0 pixels"
    )

    transform = (10.0, 0.0, 500000.0, 0.0, -10.0, 8030000.0)
    cog = _render_rgb_cog(reflectance, transform, "EPSG:32735")

    with MemoryFile(cog) as mem, mem.open() as src:
        red = src.read(1)

    assert np.isnan(red[:2, :2]).all(), "DN==0 patch must be NoData (NaN) in the written COG"
    assert not np.isnan(red[2:, 2:]).any(), "non-NoData pixels must have valid reflectance values"


def test_aoi_window_mask_clips_to_drawn_polygon() -> None:
    """A non-rectangular AOI yields a window mask that is True inside the polygon and False in the
    bounding-box corners outside it, after reprojecting the lon/lat geometry to the band CRS.
    Needs the geo extra; skips on a bare host."""
    pytest.importorskip("rasterio")

    from rasterio.warp import transform_bounds

    from services.worker.tasks.analysis import _aoi_window_mask

    # Right triangle in lon/lat: the hypotenuse cuts off the south-east bbox corner.
    triangle = {
        "type": "Polygon",
        "coordinates": [[[31.00, -17.80], [31.02, -17.80], [31.00, -17.82], [31.00, -17.80]]],
    }
    crs = "EPSG:32736"  # 31 E is east of 30 E
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, 31.00, -17.82, 31.02, -17.80)
    res = 10.0
    width = max(1, round((right - left) / res))
    height = max(1, round((top - bottom) / res))
    transform = (res, 0.0, left, 0.0, -res, top)

    mask = _aoi_window_mask(triangle, crs=crs, transform=transform, shape=(height, width))

    assert mask.shape == (height, width)
    assert mask.any()  # the triangle covers part of the window
    assert not mask.all()  # but not the whole bounding box -> clipping happened
    assert not bool(mask[-1, -1])  # the south-east corner is outside the triangle


def test_natural_color_cache_miss_enqueues_task(monkeypatch) -> None:
    """On cache miss the task is enqueued and its b64 result is decoded and returned."""
    import base64

    import rs_core.storage as _storage

    import services.worker.tasks as _tasks

    class _MissStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return False

    class _FakeResult:
        def get(self, timeout: int = 90) -> str:
            return base64.b64encode(_NC_JPEG).decode()

    class _FakeTask:
        @staticmethod
        def delay(*args: object, **kwargs: object) -> _FakeResult:
            return _FakeResult()

    monkeypatch.setattr(_storage, "S3CogStore", _MissStore)
    # render_natural_color_task is a lazy import inside the endpoint; patch the source module.
    monkeypatch.setattr(_tasks, "render_natural_color_task", _FakeTask)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/natural-color",
                json={"scene_id": "S2A_TEST", "geometry": _GEOMETRY, "pass_date": "2024-10-05"},
            )
    finally:
        _teardown()

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == _NC_JPEG


# ── All-passes orthophoto bundle (P4) ───────────────────────────────────────────


def _ok_job_result() -> dict:
    """A completed single-index series: two ok passes and one no_pass (which must be dropped)."""
    return {
        "status": "ok",
        "index": "ndvi",
        "mode": "dates",
        "requested": 3,
        "resolved": 2,
        "passes": [
            {"status": "ok", "scene_id": "S2A", "pass_date": "2024-10-01", "index": "ndvi"},
            {"status": "ok", "scene_id": "S2B", "pass_date": "2024-10-11", "index": "ndvi"},
            {"status": "no_pass", "requested_date": "2024-10-21", "index": "ndvi"},
        ],
    }


def _patch_async_result(monkeypatch, state: str, result: dict | None) -> None:
    """Stub celery.result.AsyncResult (lazy-imported by the endpoint) with a fixed state/result."""
    import celery.result as _cr

    class _AR:
        def __init__(self, job_id: str, app=None) -> None:
            self.id = job_id

        @property
        def state(self) -> str:
            return state

        @property
        def result(self):
            return result

    monkeypatch.setattr(_cr, "AsyncResult", _AR)


def _patch_bundle_apply(monkeypatch) -> list[dict]:
    import services.worker.tasks as _tasks

    calls: list[dict] = []
    monkeypatch.setattr(
        _tasks.bundle_aoi_orthophotos_task, "apply_async", lambda **k: calls.append(k)
    )
    return calls


def test_start_orthophoto_bundle_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/analyse/aoi/jobs/job1/orthophoto-bundle", json={"geometry": _GEOMETRY})
    assert resp.status_code == 401


def test_start_orthophoto_bundle_enqueues_202(monkeypatch) -> None:
    """A completed job's ok passes (deduped, no_pass dropped) are handed to the bulk task at the
    bundle key, and the task id is returned for polling."""
    _patch_async_result(monkeypatch, "SUCCESS", _ok_job_result())
    calls = _patch_bundle_apply(monkeypatch)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/jobs/job1/orthophoto-bundle", json={"geometry": _GEOMETRY}
            )
    finally:
        _teardown()

    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "queued"
    assert body["bundle_job_id"]

    assert len(calls) == 1
    geometry, passes, bundle_key = calls[0]["args"]
    assert passes == [["S2A", "2024-10-01"], ["S2B", "2024-10-11"]]
    assert calls[0]["task_id"] == body["bundle_job_id"]
    assert bundle_key.endswith(f"{body['bundle_job_id']}.zip")


def test_start_orthophoto_bundle_job_not_done_409(monkeypatch) -> None:
    _patch_async_result(monkeypatch, "PENDING", None)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/jobs/job1/orthophoto-bundle", json={"geometry": _GEOMETRY}
            )
    finally:
        _teardown()
    assert resp.status_code == 409


def test_start_orthophoto_bundle_no_ok_passes_422(monkeypatch) -> None:
    _patch_async_result(
        monkeypatch,
        "SUCCESS",
        {"status": "ok", "passes": [{"status": "no_pass", "requested_date": "2024-10-21"}]},
    )
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/analyse/aoi/jobs/job1/orthophoto-bundle", json={"geometry": _GEOMETRY}
            )
    finally:
        _teardown()
    assert resp.status_code == 422


def test_orthophoto_bundle_download_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/analyse/aoi/orthophoto-bundle/B1/download").status_code == 401


def test_orthophoto_bundle_download_returns_302(monkeypatch) -> None:
    import rs_core.storage as _storage

    class _HitStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return True

        def presigned_url(
            self, key: str, *, filename: str | None = None, expires: int = 900
        ) -> str:
            return _PRESIGNED

    monkeypatch.setattr(_storage, "S3CogStore", _HitStore)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.get("/analyse/aoi/orthophoto-bundle/B1/download", follow_redirects=False)
    finally:
        _teardown()
    assert resp.status_code == 302
    assert resp.headers["location"] == _PRESIGNED


def test_orthophoto_bundle_download_missing_is_404(monkeypatch) -> None:
    import rs_core.storage as _storage

    class _MissingStore:
        def __init__(self, _settings):
            pass

        def exists(self, key: str) -> bool:
            return False

    monkeypatch.setattr(_storage, "S3CogStore", _MissingStore)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.get("/analyse/aoi/orthophoto-bundle/B1/download")
    finally:
        _teardown()
    assert resp.status_code == 404


def test_orthophoto_bundle_download_no_storage_is_503(monkeypatch) -> None:
    import rs_core.storage as _storage

    def _no_boto3(_settings):
        raise ImportError("boto3 not installed")

    monkeypatch.setattr(_storage, "S3CogStore", _no_boto3)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.get("/analyse/aoi/orthophoto-bundle/B1/download")
    finally:
        _teardown()
    assert resp.status_code == 503
