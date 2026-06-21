"""Tests for the AOI Studio download endpoints (0020 / 0021).

No Celery broker, no MinIO, no DB required -- all external deps are monkeypatched.
"""

from __future__ import annotations

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
