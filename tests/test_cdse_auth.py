"""Phase 0 tests for the CDSE OAuth2 client: token caching, transparent refresh before
expiry, refresh-token grant with fallback, and transient-error retry. Zero network: an
injected httpx.MockTransport plays the token endpoint and a fake clock drives expiry."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
from rs_core.config import Settings
from rs_imagery import CdseOAuth2Client

_TOKEN_URL = "https://identity.example/realms/CDSE/protocol/openid-connect/token"


def _settings() -> Settings:
    return Settings(
        cdse_token_url=_TOKEN_URL,
        cdse_client_id="rs-machine",
        cdse_client_secret="shhh",
    )


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _transport(responses: list[httpx.Response]) -> tuple[httpx.MockTransport, list[dict]]:
    """Return a transport that serves `responses` in order, recording each request's form
    body as a parsed dict for assertions."""
    calls: list[dict] = []
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        calls.append(body)
        return remaining.pop(0)

    return httpx.MockTransport(handler), calls


def _ok(access_token: str, *, expires_in: int = 600, refresh_token: str | None = None):
    payload: dict[str, object] = {"access_token": access_token, "expires_in": expires_in}
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    return httpx.Response(200, json=payload)


def _make_client(
    responses: list[httpx.Response], clock: _FakeClock
) -> tuple[CdseOAuth2Client, list[dict]]:
    transport, calls = _transport(responses)
    http = httpx.AsyncClient(transport=transport)
    client = CdseOAuth2Client(
        _settings(),
        client=http,
        clock=clock,
        wait_min=0.0,
        wait_max=0.0,
        wait_multiplier=0.0,
    )
    return client, calls


def test_missing_token_url_raises():
    with pytest.raises(ValueError):
        CdseOAuth2Client(Settings())


async def test_acquires_via_client_credentials():
    clock = _FakeClock()
    client, calls = _make_client([_ok("tok-1")], clock)
    assert await client.token() == "tok-1"
    assert await client.authorization_header() == {"Authorization": "Bearer tok-1"}
    assert calls[0]["grant_type"] == "client_credentials"
    assert calls[0]["client_id"] == "rs-machine"


async def test_token_is_cached_until_expiry():
    clock = _FakeClock()
    client, calls = _make_client([_ok("tok-1", expires_in=600)], clock)
    assert await client.token() == "tok-1"
    clock.now = 100.0  # still within (600 - 60s margin) = 540s
    assert await client.token() == "tok-1"
    assert len(calls) == 1, "second call within validity must not hit the endpoint"


async def test_refreshes_after_expiry_with_refresh_grant():
    clock = _FakeClock()
    client, calls = _make_client(
        [_ok("tok-1", expires_in=600, refresh_token="r-1"), _ok("tok-2", expires_in=600)],
        clock,
    )
    assert await client.token() == "tok-1"
    clock.now = 1000.0  # past expiry
    assert await client.token() == "tok-2"
    assert len(calls) == 2
    assert calls[1]["grant_type"] == "refresh_token"
    assert calls[1]["refresh_token"] == "r-1"


async def test_falls_back_to_credentials_when_refresh_rejected():
    clock = _FakeClock()
    client, calls = _make_client(
        [
            _ok("tok-1", expires_in=600, refresh_token="r-stale"),
            httpx.Response(400, json={"error": "invalid_grant"}),
            _ok("tok-2", expires_in=600),
        ],
        clock,
    )
    assert await client.token() == "tok-1"
    clock.now = 1000.0
    assert await client.token() == "tok-2"
    assert [c["grant_type"] for c in calls] == [
        "client_credentials",
        "refresh_token",
        "client_credentials",
    ]


async def test_retries_transient_errors():
    clock = _FakeClock()
    client, calls = _make_client(
        [httpx.Response(503), httpx.Response(429), _ok("tok-1")],
        clock,
    )
    assert await client.token() == "tok-1"
    assert len(calls) == 3, "two transient failures should be retried"


async def test_does_not_retry_auth_errors():
    clock = _FakeClock()
    client, calls = _make_client([httpx.Response(401, json={"error": "unauthorized"})], clock)
    with pytest.raises(httpx.HTTPStatusError):
        await client.token()
    assert len(calls) == 1, "401 is not transient and must not be retried"
