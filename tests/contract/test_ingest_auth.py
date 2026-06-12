"""Contract pin for the S4.6 ingest-auth migration on the EXTERNAL-FROZEN ``POST /ingest/farm``
(confirmed 2026-06-12: the live gateway still calls it).

Two modes, both pinned. Enforcement OFF (the shipped default): a keyless or wrong-key call
behaves exactly as the frozen contract always has - the gate admits it and the request
proceeds (here to a 422 on an invalid body, proving the gate was not what stopped it).
Enforcement ON (the ⚑ CONFIRM flip, once the gateway sends the key): missing/wrong key is 401,
an unconfigured key fails closed at 500, and the correct key admits the call - mirroring the
``/api/v1/mobile/*`` semantics with the same shared key. Zero-DB: the session dependency is
overridden; an invalid body keeps every admitted request inside validation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from rs_core.config import Settings, get_settings
from rs_core.db import get_read_session, get_session

from services.api.main import app

_KEY = "shared-gateway-key"
# Invalid on purpose: canonical_farm_id is required, so any admitted request stops at 422.
_BAD_BODY: dict[str, object] = {}


async def _no_session() -> AsyncIterator[None]:
    yield None


def _client(settings: Settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = _no_session
    app.dependency_overrides[get_read_session] = _no_session
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client_enforcement_off() -> Iterator[TestClient]:
    yield from _client(Settings(_env_file=None, agritrack_api_key=_KEY, ingest_require_key=False))


@pytest.fixture
def client_enforcement_on() -> Iterator[TestClient]:
    yield from _client(Settings(_env_file=None, agritrack_api_key=_KEY, ingest_require_key=True))


@pytest.fixture
def client_on_unconfigured() -> Iterator[TestClient]:
    yield from _client(Settings(_env_file=None, agritrack_api_key="", ingest_require_key=True))


def test_off_keyless_call_is_admitted(client_enforcement_off: TestClient) -> None:
    # The frozen behavior: no key has always worked. 422 = the gate let it through.
    resp = client_enforcement_off.post("/ingest/farm", json=_BAD_BODY)
    assert resp.status_code == 422


def test_off_wrong_key_is_admitted_and_only_logged(client_enforcement_off: TestClient) -> None:
    resp = client_enforcement_off.post(
        "/ingest/farm", json=_BAD_BODY, headers={"X-Api-Key": "wrong"}
    )
    assert resp.status_code == 422


def test_on_missing_key_is_401(client_enforcement_on: TestClient) -> None:
    resp = client_enforcement_on.post("/ingest/farm", json=_BAD_BODY)
    assert resp.status_code == 401


def test_on_wrong_key_is_401(client_enforcement_on: TestClient) -> None:
    resp = client_enforcement_on.post("/ingest/farm", json=_BAD_BODY, headers={"X-Api-Key": "no"})
    assert resp.status_code == 401


def test_on_correct_key_is_admitted(client_enforcement_on: TestClient) -> None:
    resp = client_enforcement_on.post("/ingest/farm", json=_BAD_BODY, headers={"X-Api-Key": _KEY})
    assert resp.status_code == 422


def test_on_unconfigured_key_fails_closed_500(client_on_unconfigured: TestClient) -> None:
    resp = client_on_unconfigured.post(
        "/ingest/farm", json=_BAD_BODY, headers={"X-Api-Key": "anything"}
    )
    assert resp.status_code == 500


# Starlette decodes headers as latin-1, and compare_digest raises TypeError on non-ASCII str: a
# garbage key must compare false (401 enforced) or pass through (422 off), never 500. Sent as
# latin-1 bytes because the httpx client refuses non-ASCII str values; the server still sees the
# decoded non-ASCII string these pin against. One test per mode: the fixtures override settings
# on the shared app, so two clients cannot coexist in one test.
_GARBAGE_KEY = {"X-Api-Key": "clé-väl".encode("latin-1")}


def test_off_non_ascii_key_is_admitted_not_500(client_enforcement_off: TestClient) -> None:
    resp = client_enforcement_off.post("/ingest/farm", json=_BAD_BODY, headers=_GARBAGE_KEY)
    assert resp.status_code == 422


def test_on_non_ascii_key_is_401_not_500(client_enforcement_on: TestClient) -> None:
    resp = client_enforcement_on.post("/ingest/farm", json=_BAD_BODY, headers=_GARBAGE_KEY)
    assert resp.status_code == 401
