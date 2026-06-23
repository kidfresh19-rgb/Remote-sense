"""Unit tests for CDSE quota governance (S4.5, R-3/R18): the pure token-bucket math, the circuit
breaker state machine, the Redis-backed buckets against in-memory fakes (mirroring the Lua
semantics, like the lock tests), and the wiring into the STAC client. Zero network, zero Redis;
Lua-vs-Python parity against a real Redis lives in test_resilience_redis.py."""

from __future__ import annotations

import threading

import httpx
import pytest
from rs_core.config import Settings
from rs_imagery.adapters.cdse_stac import CdseStacClient
from rs_imagery.resilience import (
    AsyncTokenBucket,
    CircuitBreaker,
    CircuitOpenError,
    PermanentError,
    QuotaWaitExceeded,
    SyncTokenBucket,
    async_bucket_from_settings,
    refill_and_consume,
    sync_bucket_from_settings,
)
from rs_imagery.types import AOI, TimeRange

# -- pure bucket math --------------------------------------------------------------------------


def test_full_bucket_allows_and_consumes() -> None:
    tokens, ts, allowed, retry_s = refill_and_consume(
        10.0, 1_000, 1_000, capacity=10.0, rate_per_s=2.0, cost=1.0
    )
    assert allowed is True
    assert tokens == 9.0
    assert ts == 1_000
    assert retry_s == 0.0


def test_refill_is_capped_at_capacity() -> None:
    tokens, _, allowed, _ = refill_and_consume(
        0.0, 0, 3_600_000, capacity=5.0, rate_per_s=10.0, cost=1.0
    )
    assert allowed is True
    assert tokens == 4.0  # refilled to 5 (the cap), minus the cost


def test_empty_bucket_denies_with_retry_hint() -> None:
    tokens, ts, allowed, retry_s = refill_and_consume(
        0.0, 1_000, 1_000, capacity=10.0, rate_per_s=2.0, cost=1.0
    )
    assert allowed is False
    assert tokens == 0.0
    assert ts == 1_000  # state stays fresh even on deny
    assert retry_s == pytest.approx(0.5)  # 1 token at 2/s


def test_clock_skew_backwards_never_credits() -> None:
    tokens, _, allowed, _ = refill_and_consume(
        1.0, 10_000, 5_000, capacity=10.0, rate_per_s=2.0, cost=1.0
    )
    assert allowed is True
    assert tokens == 0.0  # no refill from a backwards clock


# -- circuit breaker ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_threshold_and_recovers_via_half_open() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=60.0, clock=clock)

    assert breaker.allow()
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()  # open

    clock.now = 59.0
    assert not breaker.allow()  # still open
    clock.now = 61.0
    assert breaker.allow()  # half-open: one probe
    assert not breaker.allow()  # no second probe while the first is in flight
    breaker.record_success()
    assert breaker.allow()  # closed again
    breaker.record_failure()
    assert breaker.allow()  # one failure < threshold: still closed


def test_breaker_half_open_failure_reopens() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=10.0, clock=clock)
    breaker.record_failure()
    assert not breaker.allow()
    clock.now = 11.0
    assert breaker.allow()  # probe
    breaker.record_failure()
    assert not breaker.allow()  # reopened
    clock.now = 21.0
    assert breaker.allow()


def test_breaker_success_resets_the_failure_count() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=10.0, clock=_Clock())
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.allow()  # 1 consecutive failure, threshold 2


def _hammer_record_failure(breaker: CircuitBreaker, n: int) -> None:
    """Fire `n` concurrent record_failure calls, released together to maximise contention."""
    barrier = threading.Barrier(n)

    def hit() -> None:
        barrier.wait()
        breaker.record_failure()

    threads = [threading.Thread(target=hit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_breaker_counts_concurrent_failures_without_losing_increments() -> None:
    # AOI Studio runs band reads in a thread pool (ADR 0011), so record_failure races. Without
    # the lock, read-modify-write races would drop increments and the circuit would never open.
    n = 200
    breaker = CircuitBreaker(failure_threshold=n, reset_timeout_s=60.0, clock=_Clock())
    _hammer_record_failure(breaker, n)
    assert not breaker.allow()  # all n landed: the n-th tripped it open


def test_breaker_does_not_overcount_concurrent_failures() -> None:
    n = 200
    breaker = CircuitBreaker(failure_threshold=n + 1, reset_timeout_s=60.0, clock=_Clock())
    _hammer_record_failure(breaker, n)
    assert breaker.allow()  # exactly n failures < threshold (n+1): still closed


async def test_breaker_call_guards_and_records() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=30.0, clock=clock)

    async def boom() -> None:
        raise RuntimeError("cdse down")

    with pytest.raises(RuntimeError):
        await breaker.call(boom)
    with pytest.raises(CircuitOpenError):
        await breaker.call(boom)  # open: not even attempted

    clock.now = 31.0

    async def ok() -> str:
        return "fine"

    assert await breaker.call(ok) == "fine"
    assert breaker.allow()


def test_breaker_does_not_trip_on_permanent_errors() -> None:
    # A run of permanent failures (a missing / forbidden / LTA-offline object) must never open the
    # circuit: the store is healthy, the data simply is not there. Threshold 1 makes the point
    # sharply - a single *counted* failure would open it, yet the breaker stays closed.
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=60.0, clock=_Clock())

    def gone() -> None:
        raise PermanentError("404 Not Found")

    for _ in range(5):
        with pytest.raises(PermanentError):
            breaker.call_sync(gone)
    assert breaker.allow()  # never tripped, despite threshold 1


def test_breaker_permanent_error_breaks_the_failure_streak() -> None:
    # A permanent error counts as a health success (CDSE answered, so it is reachable), so it
    # resets the consecutive-failure count rather than being ignored outright.
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=60.0, clock=_Clock())
    breaker.record_failure()  # 1 transient failure (threshold 2)

    with pytest.raises(PermanentError):
        breaker.call_sync(lambda: (_ for _ in ()).throw(PermanentError("AccessDenied")))

    breaker.record_failure()  # would be the 2nd-in-a-row and trip it, but the streak was reset
    assert breaker.allow()  # 1 < 2: still closed, proving the permanent error reset the count


async def test_breaker_call_does_not_trip_on_permanent_errors() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=60.0, clock=_Clock())

    async def gone() -> None:
        raise PermanentError("NoSuchKey")

    with pytest.raises(PermanentError):
        await breaker.call(gone)
    assert breaker.allow()  # the async path spares the breaker the same way


# -- Redis-backed buckets against in-memory fakes ----------------------------------------------


class _FakeQuotaState:
    """Shared bucket state for the fakes, applying the same math as the Lua script (the parity
    test against real Redis pins the two together)."""

    def __init__(self) -> None:
        self.tokens: float | None = None
        self.ts: int | None = None
        self.calls = 0

    def consume(self, args: tuple) -> list[int]:
        capacity, rate, now_ms, cost = (float(a) for a in args)
        self.calls += 1
        tokens = self.tokens if self.tokens is not None else capacity
        ts = self.ts if self.ts is not None else int(now_ms)
        new_tokens, new_ts, allowed, retry_s = refill_and_consume(
            tokens, ts, int(now_ms), capacity=capacity, rate_per_s=rate, cost=cost
        )
        self.tokens, self.ts = new_tokens, new_ts
        return [1 if allowed else 0, int(retry_s * 1000 + 0.5)]


class _FakeAsyncRedis:
    def __init__(self, state: _FakeQuotaState, *, raise_errors: bool = False) -> None:
        self._state = state
        self._raise = raise_errors

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> list[int]:
        if self._raise:
            raise ConnectionError("redis down")
        return self._state.consume(tuple(keys_and_args[numkeys:]))


class _FakeSyncRedis:
    def __init__(self, state: _FakeQuotaState) -> None:
        self._state = state

    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> list[int]:
        return self._state.consume(tuple(keys_and_args[numkeys:]))


async def test_async_bucket_waits_for_refill_then_proceeds() -> None:
    state = _FakeQuotaState()
    clock = _Clock()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds  # sleeping advances the clock, refilling the bucket

    bucket = AsyncTokenBucket(
        _FakeAsyncRedis(state),
        capacity=1.0,
        rate_per_s=2.0,
        clock=clock,
        sleep=fake_sleep,
    )
    await bucket.acquire()  # the initial burst token
    await bucket.acquire()  # must wait ~0.5 s for the refill
    assert sleeps == [pytest.approx(0.5)]
    assert state.calls == 3  # allow, deny, allow


async def test_async_bucket_raises_when_the_wait_exceeds_the_cap() -> None:
    state = _FakeQuotaState()
    clock = _Clock()

    async def fake_sleep(seconds: float) -> None:
        clock.now += seconds

    bucket = AsyncTokenBucket(
        _FakeAsyncRedis(state), capacity=1.0, rate_per_s=0.001, clock=clock, sleep=fake_sleep
    )
    await bucket.acquire()
    with pytest.raises(QuotaWaitExceeded):
        await bucket.acquire(max_wait_s=1.0)  # the next token is ~1000 s away


async def test_async_bucket_fails_open_when_redis_is_down() -> None:
    bucket = AsyncTokenBucket(
        _FakeAsyncRedis(_FakeQuotaState(), raise_errors=True), capacity=1.0, rate_per_s=1.0
    )
    await bucket.acquire()  # quota protection must never take collection down with it


def test_sync_bucket_waits_for_refill_then_proceeds() -> None:
    state = _FakeQuotaState()
    clock = _Clock()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    bucket = SyncTokenBucket(
        _FakeSyncRedis(state), capacity=1.0, rate_per_s=2.0, clock=clock, sleep=fake_sleep
    )
    bucket.acquire()
    bucket.acquire()
    assert sleeps == [pytest.approx(0.5)]


# -- settings factories ------------------------------------------------------------------------
# Every Settings here passes _env_file=None: a developer's real .env (e.g. a configured CDSE
# rate limit) must never flip these outcomes, in either direction.


def test_buckets_are_off_until_a_rate_is_configured() -> None:
    settings = Settings(_env_file=None)
    assert async_bucket_from_settings(settings) is None
    assert sync_bucket_from_settings(settings) is None


def test_empty_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships `RS_..=` empty meaning "use the default"; the numeric-optional fields
    # must treat that as unset, not crash at boot (env_ignore_empty).
    monkeypatch.setenv("RS_CDSE_RATE_LIMIT_RPS", "")
    monkeypatch.setenv("RS_COG_RETENTION_MONTHS", "")
    settings = Settings(_env_file=None)
    assert settings.cdse_rate_limit_rps is None
    assert settings.cog_retention_months is None


def test_buckets_build_from_a_configured_rate() -> None:
    settings = Settings(_env_file=None, cdse_rate_limit_rps=2.0, cdse_rate_limit_burst=8.0)
    assert async_bucket_from_settings(settings) is not None
    assert sync_bucket_from_settings(settings) is not None


# -- wiring: the STAC client consumes quota and trips the breaker ------------------------------


def _stac_settings() -> Settings:
    return Settings(
        _env_file=None, cdse_stac_url="https://stac.test", cdse_stac_collection="sentinel-2-l2a"
    )


def _aoi() -> AOI:
    square = {
        "type": "Polygon",
        "coordinates": [
            [[31.0, -17.9], [31.1, -17.9], [31.1, -17.8], [31.0, -17.8], [31.0, -17.9]]
        ],
    }
    return AOI(geometry=square, crs="EPSG:4326")


def _range() -> TimeRange:
    from datetime import UTC, datetime

    return TimeRange(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC))


async def test_search_acquires_quota_before_the_request() -> None:
    state = _FakeQuotaState()
    bucket = AsyncTokenBucket(_FakeAsyncRedis(state), capacity=5.0, rate_per_s=1.0)
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"features": []})

    client = CdseStacClient(
        _stac_settings(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        bucket=bucket,
    )
    await client.search_items(_aoi(), _range())
    assert requests == 1
    assert state.calls == 1
    assert state.tokens == 4.0


async def test_breaker_opens_after_exhausted_searches_and_blocks_the_next() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503)

    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=60.0, clock=clock)
    client = CdseStacClient(
        _stac_settings(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        breaker=breaker,
        max_attempts=2,
        wait_min=0.0,
        wait_max=0.0,
        wait_multiplier=0.0,
    )
    for _ in range(2):  # two searches, each exhausting its retries = two breaker failures
        with pytest.raises(httpx.HTTPStatusError):
            await client.search_items(_aoi(), _range())
    assert requests == 4  # 2 searches x 2 attempts

    with pytest.raises(CircuitOpenError):
        await client.search_items(_aoi(), _range())
    assert requests == 4  # the open breaker never reached the transport
