"""Contract auth gate: the frozen AgriTrack routes require ``X-Api-Key``.

Verifies that ``POST /api/v1/mobile/sync`` and ``GET /api/v1/mobile/data`` enforce the shared key
(ADR 0006, ``CONTRACT.md``), failing closed: 500 when the key is unconfigured, 401 when the caller
omits or mismatches it. Zero-DB: ``get_session`` is overridden so the auth gate is exercised without
a database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from rs_core.config import Settings, get_settings
from rs_core.db import get_session

from services.api.main import app

# (method, path, json-body). Bodies are valid so the request reaches the auth dependency, not a 422.
FROZEN_AGRITRACK = (
    ("post", "/api/v1/mobile/sync", {"farms": []}),
    ("get", "/api/v1/mobile/data?farm_id=1", None),
)


async def _no_session() -> AsyncIterator[None]:
    """Stand in for get_session: the auth gate rejects before any DB use, so yield a sentinel."""
    yield None


def _client(api_key: str) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = _no_session
    app.dependency_overrides[get_settings] = lambda: Settings(agritrack_api_key=api_key)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _request(
    client: TestClient, method: str, path: str, body: object, headers: dict[str, str] | None = None
) -> object:
    """Call the endpoint, sending a JSON body only for methods that take one (GET takes none)."""
    kwargs: dict[str, object] = {} if body is None else {"json": body}
    if headers:
        kwargs["headers"] = headers
    return getattr(client, method)(path, **kwargs)


@pytest.fixture
def client_with_key() -> Iterator[TestClient]:
    yield from _client("testkey")


@pytest.fixture
def client_no_key() -> Iterator[TestClient]:
    yield from _client("")


@pytest.mark.parametrize("method,path,body", FROZEN_AGRITRACK)
def test_missing_key_is_401(
    client_with_key: TestClient, method: str, path: str, body: object
) -> None:
    resp = _request(client_with_key, method, path, body)
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path,body", FROZEN_AGRITRACK)
def test_wrong_key_is_401(
    client_with_key: TestClient, method: str, path: str, body: object
) -> None:
    resp = _request(client_with_key, method, path, body, {"X-Api-Key": "wrong"})
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path,body", FROZEN_AGRITRACK)
def test_unconfigured_key_fails_closed_500(
    client_no_key: TestClient, method: str, path: str, body: object
) -> None:
    resp = _request(client_no_key, method, path, body, {"X-Api-Key": "anything"})
    assert resp.status_code == 500
