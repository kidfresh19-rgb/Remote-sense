"""A small async JSON cache over Redis for the AOI Studio preview read path (ADR 0011): a
per-pass result cache and a STAC search cache, both keyed on immutable inputs.

Every operation FAILS OPEN - a miss and any Redis (or decode) error alike return None on `get`
and no-op on `set`, so a degraded cache silently falls back to a live read and never fails a
preview. This mirrors the quota bucket's fail-open rule (rs_imagery.resilience). The client is a
structural Protocol (the services/worker/locks.py pattern), so tests inject an in-memory fake
with no Redis."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any, Protocol

from rs_core.config import Settings
from rs_core.logging import get_logger

log = get_logger("rs_core.cache")


class AsyncCacheClient(Protocol):
    """The slice of a `redis.asyncio` client the JSON cache needs. Declared as sync methods
    returning Awaitable (how redis.asyncio types its commands); an `async def` fake still
    matches structurally."""

    def get(self, name: str) -> Awaitable[Any]: ...

    def set(self, name: str, value: str, *, ex: int | None = ...) -> Awaitable[Any]: ...


class RedisJsonCache:
    """An async JSON cache over a namespaced Redis string keyspace. `get` returns the decoded
    value or None (a miss or any failure); `set` stores JSON with a TTL. All Redis and JSON
    errors are swallowed and logged - a cache must never fail a preview (fail-open)."""

    def __init__(self, client: AsyncCacheClient, *, namespace: str) -> None:
        self._client = client
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self._client.get(self._key(key))
        except Exception as exc:  # noqa: BLE001 - fail open: a cache never fails a preview
            log.warning("cache.get.fail_open", namespace=self._namespace, error=str(exc))
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            log.warning("cache.decode.error", namespace=self._namespace, error=str(exc))
            return None

    async def set(self, key: str, value: Any, *, ttl_s: int) -> None:
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError) as exc:
            log.warning("cache.encode.error", namespace=self._namespace, error=str(exc))
            return
        try:
            await self._client.set(self._key(key), payload, ex=ttl_s)
        except Exception as exc:  # noqa: BLE001 - fail open
            log.warning("cache.set.fail_open", namespace=self._namespace, error=str(exc))

    async def aclose(self) -> None:
        """Release the underlying client's connection pool when it has one (a real
        redis.asyncio client does; an in-memory fake does not). Never raises into a task."""
        closer = getattr(self._client, "aclose", None)
        if closer is None:
            return
        try:
            await closer()
        except Exception as exc:  # noqa: BLE001 - closing must never raise into a task
            log.warning("cache.close.error", namespace=self._namespace, error=str(exc))


def redis_json_cache_from_settings(settings: Settings, *, namespace: str) -> RedisJsonCache | None:
    """Build an async JSON cache on the shared Redis under `namespace`, or None when no Redis URL
    is configured. Lazy: constructing the client opens no connection until the first call."""
    if not settings.redis_url:
        return None
    import redis.asyncio as aredis

    client = aredis.from_url(settings.redis_url, decode_responses=True)
    return RedisJsonCache(client, namespace=namespace)
