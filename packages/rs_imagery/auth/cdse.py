"""CDSE OAuth2 client.

Copernicus Data Space (CDSE) fronts its STAC and processing APIs with a Keycloak realm.
Machine access uses the OAuth2 ``client_credentials`` grant; when the realm also issues a
refresh token, it is used to renew quietly before falling back to a fresh credentials
grant. The client caches the bearer token and refreshes it transparently before expiry, so
adapters just call :meth:`authorization_header` per request without thinking about tokens.

This lives in the access layer on purpose (CLAUDE.md invariant 1): token management,
retry/backoff and circuit-breaking are centralized here, never scattered through callers.
The token endpoint and credentials come from config (env), never hard-coded (invariant 8).

Zero-network testable: inject an ``httpx.AsyncClient`` backed by ``httpx.MockTransport`` and
a fake ``clock`` to drive expiry deterministically."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from rs_core.config import Settings
from rs_core.logging import get_logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = get_logger("rs_imagery.cdse")

# Renew this many seconds before the token's stated expiry so an in-flight request never
# races the expiry boundary on the server side.
_EXPIRY_SAFETY_MARGIN_S = 60.0


def _is_transient(exc: BaseException) -> bool:
    """Retry on network faults and CDSE throttling/5xx; never on auth/validation 4xx."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


@dataclass
class _Token:
    access_token: str
    expires_at: float  # in the units of the injected clock
    refresh_token: str | None = None


class CdseOAuth2Client:
    """Acquires and caches a CDSE bearer token, refreshing transparently before expiry.

    One instance is shared by the real adapters. Concurrent callers are serialized by an
    asyncio lock so a token expiry triggers exactly one refresh, not a stampede."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int = 5,
        wait_min: float = 1.0,
        wait_max: float = 30.0,
        wait_multiplier: float = 1.0,
    ) -> None:
        if not settings.cdse_token_url:
            raise ValueError("RS_CDSE_TOKEN_URL is not configured; cannot acquire a CDSE token.")
        self._token_url = settings.cdse_token_url
        self._client_id = settings.cdse_client_id
        self._client_secret = settings.cdse_client_secret
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: _Token | None = None
        self._retrying = AsyncRetrying(
            retry=retry_if_exception(_is_transient),
            wait=wait_exponential(multiplier=wait_multiplier, min=wait_min, max=wait_max),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        )

    async def token(self) -> str:
        """Return a currently-valid access token, refreshing it if it has lapsed."""
        async with self._lock:
            if self._token is None or self._clock() >= self._token.expires_at:
                self._token = await self._fetch()
            return self._token.access_token

    async def authorization_header(self) -> dict[str, str]:
        """The ``Authorization`` header adapters attach to every CDSE request."""
        return {"Authorization": f"Bearer {await self.token()}"}

    async def _fetch(self) -> _Token:
        # Prefer a refresh-token grant when the realm issued one; fall back to a fresh
        # client-credentials grant if the refresh token was rejected (invalid_grant → 400).
        if self._token is not None and self._token.refresh_token:
            try:
                return await self._retrying(self._request, self._refresh_payload())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 400:
                    raise
                log.info("cdse.token.refresh_rejected_fallback")
        return await self._retrying(self._request, self._credentials_payload())

    def _credentials_payload(self) -> dict[str, str]:
        return {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }

    def _refresh_payload(self) -> dict[str, str]:
        assert self._token is not None and self._token.refresh_token is not None
        return {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._token.refresh_token,
        }

    async def _request(self, data: dict[str, str]) -> _Token:
        resp = await self._client.post(self._token_url, data=data)
        resp.raise_for_status()
        payload = resp.json()
        expires_in = float(payload.get("expires_in", 600))
        token = _Token(
            access_token=payload["access_token"],
            expires_at=self._clock() + max(0.0, expires_in - _EXPIRY_SAFETY_MARGIN_S),
            refresh_token=payload.get("refresh_token"),
        )
        log.info("cdse.token.acquired", grant=data["grant_type"], expires_in=expires_in)
        return token

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CdseOAuth2Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
