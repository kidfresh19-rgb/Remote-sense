"""BFF download endpoint (0019): 302 presigned-URL redirect instead of byte proxy.
No DB or MinIO required — the session dependency is overridden and S3CogStore is monkeypatched."""

from __future__ import annotations

from rs_core.db import get_read_session
from rs_core.rbac import Principal, Role
from starlette.testclient import TestClient

from services.api.auth import get_principal
from services.api.main import app

_FIELD = "00000000-0000-0000-0000-000000000001"
_SCENE = "S2B_MSIL2A_20241005"
_PRESIGNED = "https://minio.internal/rs-cog/cog/v1/field/scene/rgb.tif?X-Amz-Signature=abc"


def _analyst() -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="analyst", roles=frozenset({Role.ANALYST})
    )
    # geometry_version supplied → session never queried; safe to return None
    app.dependency_overrides[get_read_session] = lambda: None


def _teardown() -> None:
    app.dependency_overrides.clear()


def test_download_requires_auth() -> None:
    url = f"/fields/{_FIELD}/scenes/{_SCENE}/download?index=rgb&geometry_version=1"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401


def test_download_returns_302_presigned_url(monkeypatch) -> None:
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
                f"/fields/{_FIELD}/scenes/{_SCENE}/download?index=rgb&geometry_version=1",
                follow_redirects=False,
            )
    finally:
        _teardown()

    assert resp.status_code == 302
    assert resp.headers["location"] == _PRESIGNED


def test_download_missing_cog_is_404(monkeypatch) -> None:
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
            resp = client.get(
                f"/fields/{_FIELD}/scenes/{_SCENE}/download?index=ndvi&geometry_version=1"
            )
    finally:
        _teardown()

    assert resp.status_code == 404


def test_download_missing_storage_is_503(monkeypatch) -> None:
    import rs_core.storage as _storage

    def _no_boto3(_settings):
        raise ImportError("boto3 not installed")

    monkeypatch.setattr(_storage, "S3CogStore", _no_boto3)
    _analyst()
    try:
        with TestClient(app) as client:
            resp = client.get(
                f"/fields/{_FIELD}/scenes/{_SCENE}/download?index=rgb&geometry_version=1"
            )
    finally:
        _teardown()

    assert resp.status_code == 503
