"""GatewayPort adapters (L7). `RecordingGatewayPort` for tests and dry runs; `HttpGatewayPort`
for the real push - the one place that knows the gateway URL, auth, and wire transport
(CLAUDE.md §1.1). Resilience (retry + backoff) lives here, not in callers."""

from __future__ import annotations

import httpx

from rs_sync.inbound import (
    DeclarationsQuery,
    HouseholdDeclaration,
    HouseholdDeclarationBatch,
    synthetic_declarations,
)
from rs_sync.payload import GatewayPayload
from rs_sync.port import GatewayPort, PushResult
from rs_sync.resilience import (
    GATEWAY_PUSH_ERRORS,
    describe_push_error,
    push_retrying,
)


class RecordingGatewayPort(GatewayPort):
    """In-memory adapter: records every pushed payload and replays a configurable outcome. Dedupes
    by idempotency key so a re-push is a no-op (R-2). Used in tests and as a dry-run sink."""

    def __init__(
        self, *, ok: bool = True, declarations: HouseholdDeclarationBatch | None = None
    ) -> None:
        self._ok = ok
        self.pushed: list[GatewayPayload] = []
        self.keys: set[str] = set()
        # Inbound side (ADR 0013): a synthetic declarations batch, injectable so a test can pin its
        # own. Defaults to the shared two-household fixture (one full, one sparse).
        self._declarations = declarations if declarations is not None else synthetic_declarations()

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

    async def fetch_household_declarations(
        self, query: DeclarationsQuery
    ) -> HouseholdDeclarationBatch:
        """Replay the synthetic batch filtered by the query (in-memory, zero network), so a test can
        assert ward / id scoping without a real gateway."""

        def keep(hh: HouseholdDeclaration) -> bool:
            if query.ward_name is not None and hh.ward_name != query.ward_name:
                return False
            if (
                query.canonical_household_ids is not None
                and hh.canonical_household_id not in query.canonical_household_ids
            ):
                return False
            return True

        return HouseholdDeclarationBatch(
            contract_version=self._declarations.contract_version,
            declarations=[hh for hh in self._declarations.declarations if keep(hh)],
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
        if self._client is not None:
            return await self._push_with_client(self._client, payload)
        timeout = httpx.Timeout(self._timeout, connect=min(self._timeout, 10.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await self._push_with_client(client, payload)

    async def _push_with_client(
        self, client: httpx.AsyncClient, payload: GatewayPayload
    ) -> PushResult:
        try:
            async for attempt in push_retrying(
                max_attempts=self._max_attempts, backoff=self._backoff
            ):
                with attempt:
                    response = await self._post(client, payload)
        except GATEWAY_PUSH_ERRORS as exc:
            # Transient failures arrive here only after retries are exhausted; a permanent 4xx
            # arrives on the first attempt (retry_transient_push declined it). Either way it is a
            # dead-letter, with a reason the operator can act on.
            return PushResult(ok=False, status="error", detail=describe_push_error(exc))
        return PushResult(ok=True, status=str(response.status_code))

    async def _post(self, client: httpx.AsyncClient, payload: GatewayPayload) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": payload.idempotency_key,
            "Content-Type": "application/json",
        }
        body = payload.model_dump(mode="json")
        response = await client.post(self._url, json=body, headers=headers, timeout=self._timeout)
        response.raise_for_status()
        return response
