"""The GatewayPort: the only seam to the gateway (CLAUDE.md §1.1). One operation - push an
additive, idempotency-keyed payload. No endpoint URL, auth scheme, or vendor specifics may appear
outside an adapter; the active adapter is a config switch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from rs_sync.inbound import DeclarationsQuery, HouseholdDeclarationBatch
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

    async def fetch_household_declarations(
        self, query: DeclarationsQuery
    ) -> HouseholdDeclarationBatch:
        """Read-only inbound (additive, ADR 0013): a household's declared crop mix, planting window,
        and drone references, keyed by the canonical household id. Optional capability - the default
        raises so a push-only adapter stays valid without a body; adapters that speak the inbound
        contract (the mock sink, the real gateway) override it. Never mutates the gateway."""
        raise NotImplementedError(f"{type(self).__name__} does not expose household declarations")

    def destination_key(self) -> str:
        """A stable identifier for *where* this port delivers, folded into the outbox idempotency
        key (R-2). It stops a dry-run sink from masking a real delivery, and makes switching the
        gateway (or its target URL) re-push the same analyses to the new destination instead of
        skipping them as already-published. Adapters with a concrete endpoint override this with
        their URL; the default is the adapter type name."""
        return type(self).__name__
