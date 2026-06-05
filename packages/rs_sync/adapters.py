"""GatewayPort adapters (L7). `RecordingGatewayPort` for tests and dry runs; `HttpGatewayPort`
for the real push - the one place that knows the gateway URL, auth, and wire transport
(CLAUDE.md §1.1). Resilience (retry + backoff) lives here, not in callers."""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rs_sync.payload import GatewayPayload
from rs_sync.port import GatewayPort, PushResult

# Transient failures worth a retry: connection/timeout errors and 5xx/429 (raise_for_status).
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


class RecordingGatewayPort(GatewayPort):
    """In-memory adapter: records every pushed payload and replays a configurable outcome. Dedupes
    by idempotency key so a re-push is a no-op (R-2). Used in tests and as a dry-run sink."""

    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok
        self.pushed: list[GatewayPayload] = []
        self.keys: set[str] = set()

    async def push(self, payload: GatewayPayload) -> PushResult:
        if payload.idempotency_key in self.keys:
            return PushResult(ok=True, status="duplicate")
        self.pushed.append(payload)
        self.keys.add(payload.idempotency_key)
        return PushResult(
            ok=self._ok,
            status="ok" if self._ok else "rejected",
            detail=None if self._ok else "rejected",
        )


class HttpGatewayPort(GatewayPort):
    """POST the JSON payload to the gateway with a bearer token and an Idempotency-Key header,
    retrying transient failures with exponential backoff. URL/auth/wire format are the only
    gateway-specific knowledge in the system and come from config.

    The confirmed AgriTrack contract is in AgriTrackGatewayPort (ADR 0006); this generic Bearer
    push stays for any other gateway selected by RS_GATEWAY_ADAPTER=http."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        max_attempts: int = 4,
        backoff: float = 0.5,
    ) -> None:
        if not url:
            raise ValueError("HttpGatewayPort needs a gateway_push_url")
        self._url = url
        self._token = token
        self._client = client
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff = backoff

    def destination_key(self) -> str:
        return self._url

    async def push(self, payload: GatewayPayload) -> PushResult:
        try:
            async for attempt in AsyncRetrying(
                reraise=True,
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(multiplier=self._backoff, max=10),
                retry=retry_if_exception_type(_RETRYABLE),
            ):
                with attempt:
                    response = await self._post(payload)
        except _RETRYABLE as exc:
            return PushResult(ok=False, status="error", detail=str(exc))
        return PushResult(ok=True, status=str(response.status_code))

    async def _post(self, payload: GatewayPayload) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": payload.idempotency_key,
            "Content-Type": "application/json",
        }
        body = payload.model_dump(mode="json")
        if self._client is not None:
            response = await self._client.post(
                self._url, json=body, headers=headers, timeout=self._timeout
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._url, json=body, headers=headers, timeout=self._timeout
                )
        response.raise_for_status()
        return response
