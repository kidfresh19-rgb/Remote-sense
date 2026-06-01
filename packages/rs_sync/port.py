"""The GatewayPort: the only seam to the gateway (CLAUDE.md §1.1). One operation - push an
additive, idempotency-keyed payload. No endpoint URL, auth scheme, or vendor specifics may appear
outside an adapter; the active adapter is a config switch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from rs_sync.payload import GatewayPayload


@dataclass(frozen=True)
class PushResult:
    """Outcome of one push. `ok=False` with status `error` means it should be retried later (the
    caller dead-letters it); `status=duplicate` means the idempotency key was already delivered."""

    ok: bool
    status: str
    detail: str | None = None


class GatewayPort(ABC):
    """Push selected results to the gateway, additively and idempotently. Adapters: `recording`
    (tests / dry runs) and `http` (the real push)."""

    @abstractmethod
    async def push(self, payload: GatewayPayload) -> PushResult:
        """Deliver one farm's additive payload. Must be safe to retry with the same payload (the
        idempotency key dedupes on the gateway side)."""
