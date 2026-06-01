"""Unit tests for the Redis enqueue-lock (R-1), exercised with an in-memory fake that models
`SET NX EX` + the compare-and-delete release. No real Redis."""

from __future__ import annotations

from services.worker.locks import acquire, enqueue_lock, release


class _FakeRedis:
    """Models just enough of redis.asyncio for the lock: SET with NX, and EVAL of the
    compare-and-delete release script."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, token = keys_and_args[0], keys_and_args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


async def test_acquire_is_exclusive() -> None:
    r = _FakeRedis()
    assert await acquire(r, "k", "tokenA", ttl_seconds=60) is True
    assert await acquire(r, "k", "tokenB", ttl_seconds=60) is False  # already held


async def test_release_only_deletes_own_token() -> None:
    r = _FakeRedis()
    await acquire(r, "k", "tokenA", ttl_seconds=60)
    assert await release(r, "k", "tokenB") is False  # not our token -> no-op
    assert r.store["k"] == "tokenA"
    assert await release(r, "k", "tokenA") is True
    assert "k" not in r.store


async def test_enqueue_lock_yields_true_then_releases() -> None:
    r = _FakeRedis()
    async with enqueue_lock(r, "k") as acquired:
        assert acquired is True
        assert "k" in r.store  # held inside the block
    assert "k" not in r.store  # released on exit


async def test_enqueue_lock_yields_false_when_held() -> None:
    r = _FakeRedis()
    r.store["k"] = "held-by-another"
    async with enqueue_lock(r, "k") as acquired:
        assert acquired is False
    assert r.store["k"] == "held-by-another"  # another worker's lock left untouched


async def test_enqueue_lock_releases_even_if_body_raises() -> None:
    r = _FakeRedis()
    with_error = False
    try:
        async with enqueue_lock(r, "k") as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    except RuntimeError:
        with_error = True
    assert with_error
    assert "k" not in r.store  # lock still released on the error path
