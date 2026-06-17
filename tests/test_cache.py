"""RedisJsonCache (ADR 0011): an async JSON cache that fails open. Zero Redis - an in-memory
fake stands in for the client; the real-Redis round-trip is exercised by the cache's users."""

from __future__ import annotations

from rs_core.cache import RedisJsonCache, redis_json_cache_from_settings
from rs_core.config import Settings


class _FakeRedis:
    """Minimal async stand-in for the redis.asyncio string commands the cache uses."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self._fail = fail

    async def get(self, name: str) -> str | None:
        if self._fail:
            raise ConnectionError("redis down")
        return self.store.get(name)

    async def set(self, name: str, value: str, *, ex: int | None = None) -> None:
        if self._fail:
            raise ConnectionError("redis down")
        self.store[name] = value
        if ex is not None:
            self.ttls[name] = ex


async def test_round_trips_json_under_a_namespace() -> None:
    fake = _FakeRedis()
    cache = RedisJsonCache(fake, namespace="aoi:result")
    await cache.set("k1", {"mean": 0.42, "passes": [1, 2, 3]}, ttl_s=900)
    assert await cache.get("k1") == {"mean": 0.42, "passes": [1, 2, 3]}
    assert "aoi:result:k1" in fake.store  # the key is namespaced
    assert fake.ttls["aoi:result:k1"] == 900


async def test_missing_key_returns_none() -> None:
    cache = RedisJsonCache(_FakeRedis(), namespace="aoi:search")
    assert await cache.get("absent") is None


async def test_get_fails_open_on_redis_error() -> None:
    cache = RedisJsonCache(_FakeRedis(fail=True), namespace="aoi:result")
    assert await cache.get("k") is None  # the error is swallowed, not raised


async def test_set_fails_open_on_redis_error() -> None:
    cache = RedisJsonCache(_FakeRedis(fail=True), namespace="aoi:result")
    await cache.set("k", {"x": 1}, ttl_s=10)  # must not raise


async def test_corrupt_payload_reads_as_a_miss() -> None:
    fake = _FakeRedis()
    fake.store["aoi:result:k"] = "{not json"
    cache = RedisJsonCache(fake, namespace="aoi:result")
    assert await cache.get("k") is None


async def test_aclose_is_safe_on_a_clientless_fake() -> None:
    await RedisJsonCache(_FakeRedis(), namespace="aoi:result").aclose()  # no aclose -> no-op


def test_factory_builds_a_cache_from_settings() -> None:
    cache = redis_json_cache_from_settings(Settings(_env_file=None), namespace="aoi:result")
    assert isinstance(cache, RedisJsonCache)


def test_factory_returns_none_without_a_redis_url() -> None:
    cache = redis_json_cache_from_settings(
        Settings(_env_file=None, redis_url=""), namespace="aoi:result"
    )
    assert cache is None
