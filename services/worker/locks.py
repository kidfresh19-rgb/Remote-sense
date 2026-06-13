"""Redis advisory locks for the collection pipeline (Phase 3, R-1). An enqueue-time lock keyed
by the collection key (field / scene / geometry version) stops two workers from processing the
same unit twice.

Acquisition is `SET key token NX EX ttl` - atomic, with a TTL so a crashed worker's lock cannot
wedge the unit forever. Release is a compare-and-delete: a worker only deletes the key while it
still holds *its* token, so it can never release a lock a later holder acquired after the TTL
expired. The lock takes a small client Protocol, not a concrete client, so the logic is testable
with an in-memory fake and no real Redis."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Protocol

# Compare-and-delete: release only if the key still holds the exact token we set.
_RELEASE_IF_OWNED = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# 10 minutes: comfortably longer than a single field/scene collection, short enough that a dead
# worker's lock clears on its own.
DEFAULT_LOCK_TTL_SECONDS = 600


class LockClient(Protocol):
    """The slice of a `redis.asyncio` client the lock needs. A real client matches structurally;
    tests pass an in-memory fake. Declared as sync methods returning Awaitable (not `async def`)
    because that is how redis.asyncio types its commands; an `async def` fake still matches."""

    def set(
        self, name: str, value: str, *, nx: bool = ..., ex: int | None = ...
    ) -> Awaitable[bool | str | bytes | None]: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Awaitable[object]: ...


async def acquire(client: LockClient, key: str, token: str, *, ttl_seconds: int) -> bool:
    """Try to take the lock. True if we set our token (acquired), False if it is already held."""
    return bool(await client.set(key, token, nx=True, ex=ttl_seconds))


async def release(client: LockClient, key: str, token: str) -> bool:
    """Release the lock iff we still hold `token`. True if we deleted it, False if it was already
    someone else's (or gone)."""
    return bool(await client.eval(_RELEASE_IF_OWNED, 1, key, token))


@asynccontextmanager
async def enqueue_lock(
    client: LockClient, key: str, *, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS
) -> AsyncIterator[bool]:
    """Hold the collection lock for `key` for the duration of the block. Yields True if acquired
    (do the work) or False if another worker already holds it (skip - that is R-1 dedup, not an
    error). Releases only our own token on exit, even if the body raised."""
    token = uuid.uuid4().hex
    acquired = await acquire(client, key, token, ttl_seconds=ttl_seconds)
    try:
        yield acquired
    finally:
        if acquired:
            await release(client, key, token)
