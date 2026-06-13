"""CDSE quota governance (S4.5, R-3/R18): a Redis token bucket shared by every worker process,
plus a per-process circuit breaker. Centralised in the access layer per CLAUDE.md §2 - reactive
retry/backoff already lives in each client; this adds the proactive half (don't earn the 429s in
the first place) and the give-up half (stop sending while CDSE is down instead of burning each
task's retries).

One budget, one key: the STAC search, the Process API render, and the windowed/metadata reads all
draw from `cdse:quota`, so a mass backfill across N workers respects a single account-level rate.
The bucket math is the pure `refill_and_consume` (the reference, unit-tested with no infra); the
Lua script applies the same arithmetic atomically server-side, and a Redis-gated parity test pins
the two together. Buckets fail OPEN when Redis is unreachable: quota protection must never take
satellite collection down with it.

The breaker is a small stdlib state machine rather than pybreaker (which the §2 toolbox names):
pybreaker is sync-oriented and lives in the optional `resilience` extra the worker images don't
install, and a dependency-free breaker keeps rs_imagery testable with zero infra (§3). Per-process
state is the right scope for "this process should stop calling out for a while"."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from rs_core.config import Settings
from rs_core.logging import get_logger

log = get_logger("rs_imagery.resilience")

QUOTA_KEY = "cdse:quota"
DEFAULT_MAX_WAIT_S = 120.0


class QuotaWaitExceeded(RuntimeError):
    """The bucket could not grant a token within the caller's wait budget."""


class CircuitOpenError(RuntimeError):
    """The breaker is open: CDSE has failed repeatedly and calls are paused for the cool-off."""


def refill_and_consume(
    tokens: float,
    last_ms: int,
    now_ms: int,
    *,
    capacity: float,
    rate_per_s: float,
    cost: float,
) -> tuple[float, int, bool, float]:
    """One token-bucket step: credit the refill earned since `last_ms` (capped at capacity, never
    negative - a backwards clock credits nothing), then try to consume `cost`. Returns
    `(new_tokens, new_last_ms, allowed, retry_after_s)`; state stays fresh on a deny so the retry
    hint is exact. This is the reference the Lua script mirrors."""
    elapsed_s = max(0, now_ms - last_ms) / 1000.0
    tokens = min(capacity, tokens + elapsed_s * rate_per_s)
    if tokens >= cost:
        return tokens - cost, now_ms, True, 0.0
    return tokens, now_ms, False, (cost - tokens) / rate_per_s


# The same arithmetic as refill_and_consume, run atomically inside Redis so concurrent workers
# never double-spend a token. State is a hash {t: tokens, ts: ms}; an absent key is a full bucket
# (allows the initial burst). KEYS[1]=bucket key; ARGV=capacity, rate_per_s, now_ms, cost.
# Returns {allowed (0/1), retry_after_ms}. The TTL is twice the time-to-full: pure hygiene, the
# state regenerates from "full" if it ever expires.
_CONSUME_LUA = """
local state = redis.call('HMGET', KEYS[1], 't', 'ts')
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
local elapsed = math.max(0, now - ts) / 1000.0
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
local retry_ms = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
else
    retry_ms = math.ceil((cost - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 't', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 2000))
return {allowed, retry_ms}
"""


class AsyncQuotaClient(Protocol):
    """The slice of a `redis.asyncio` client the bucket needs (the locks.py pattern: structural,
    so tests pass an in-memory fake)."""

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Awaitable[Any]: ...


class SyncQuotaClient(Protocol):
    """The same slice of a sync `redis` client, for the rasterio read path."""

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any: ...


def _parse_reply(reply: Any) -> tuple[bool, float]:
    allowed, retry_ms = reply
    return bool(int(allowed)), int(retry_ms) / 1000.0


class AsyncTokenBucket:
    """Cross-process CDSE request budget for the async clients (STAC search, Process API). One
    `acquire` per outbound request; it sleeps on the bucket's own retry hint until a token is
    granted or `max_wait_s` is exhausted."""

    def __init__(
        self,
        client: AsyncQuotaClient,
        *,
        capacity: float,
        rate_per_s: float,
        key: str = QUOTA_KEY,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if capacity <= 0 or rate_per_s <= 0:
            raise ValueError("capacity and rate_per_s must be positive")
        self._client = client
        self._capacity = capacity
        self._rate = rate_per_s
        self._key = key
        # One clock drives both the bucket timestamps and the wait deadline - they must agree or
        # the refill math and the budget accounting drift apart. Wall clock by default: the Redis
        # state is shared across worker processes, so timestamps must be comparable between them
        # (modest NTP skew is acceptable for an advisory quota).
        self._clock = clock
        self._sleep = sleep

    async def acquire(self, cost: float = 1.0, *, max_wait_s: float = DEFAULT_MAX_WAIT_S) -> None:
        deadline = self._clock() + max_wait_s
        while True:
            try:
                reply = await self._client.eval(
                    _CONSUME_LUA,
                    1,
                    self._key,
                    self._capacity,
                    self._rate,
                    int(self._clock() * 1000),
                    cost,
                )
            except Exception as exc:  # noqa: BLE001 - fail open: quota must never block collection
                log.warning("cdse.quota.fail_open", error=str(exc))
                return
            allowed, retry_after_s = _parse_reply(reply)
            if allowed:
                return
            if self._clock() + retry_after_s > deadline:
                raise QuotaWaitExceeded(
                    f"CDSE quota: next token in {retry_after_s:.1f}s exceeds the "
                    f"{max_wait_s:.1f}s wait budget"
                )
            await self._sleep(retry_after_s)


class SyncTokenBucket:
    """The same budget for the sync rasterio read path (windowed reads, metadata bytes). Shares
    the Redis key with the async bucket, so reads and searches draw from one CDSE budget."""

    def __init__(
        self,
        client: SyncQuotaClient,
        *,
        capacity: float,
        rate_per_s: float,
        key: str = QUOTA_KEY,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if capacity <= 0 or rate_per_s <= 0:
            raise ValueError("capacity and rate_per_s must be positive")
        self._client = client
        self._capacity = capacity
        self._rate = rate_per_s
        self._key = key
        # Same single-clock rule as AsyncTokenBucket (see there for why wall clock).
        self._clock = clock
        self._sleep = sleep

    def acquire(self, cost: float = 1.0, *, max_wait_s: float = DEFAULT_MAX_WAIT_S) -> None:
        deadline = self._clock() + max_wait_s
        while True:
            try:
                reply = self._client.eval(
                    _CONSUME_LUA,
                    1,
                    self._key,
                    self._capacity,
                    self._rate,
                    int(self._clock() * 1000),
                    cost,
                )
            except Exception as exc:  # noqa: BLE001 - fail open: quota must never block collection
                log.warning("cdse.quota.fail_open", error=str(exc))
                return
            allowed, retry_after_s = _parse_reply(reply)
            if allowed:
                return
            if self._clock() + retry_after_s > deadline:
                raise QuotaWaitExceeded(
                    f"CDSE quota: next token in {retry_after_s:.1f}s exceeds the "
                    f"{max_wait_s:.1f}s wait budget"
                )
            self._sleep(retry_after_s)


class CircuitBreaker:
    """CLOSED -> OPEN after `failure_threshold` consecutive failures; OPEN -> HALF_OPEN after
    `reset_timeout_s` (one probe call); the probe's outcome closes or reopens the circuit. A
    failure here is a *final* failure - record it after a client's retry loop is exhausted, not
    per attempt. Single-loop/single-thread use per process; no locking by design."""

    _CLOSED, _OPEN, _HALF_OPEN = "closed", "open", "half_open"

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_timeout_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0 or reset_timeout_s <= 0:
            raise ValueError("failure_threshold and reset_timeout_s must be positive")
        self._threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._state = self._CLOSED
        self._failures = 0
        self._opened_at = 0.0

    def allow(self) -> bool:
        """Whether a call may proceed now. Moving OPEN -> HALF_OPEN admits exactly one probe;
        further calls are refused until the probe reports back."""
        if self._state == self._CLOSED:
            return True
        if self._state == self._OPEN:
            if self._clock() - self._opened_at >= self._reset_timeout_s:
                self._state = self._HALF_OPEN
                return True
            return False
        return False  # half-open: the probe is already in flight

    def record_success(self) -> None:
        self._state = self._CLOSED
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == self._HALF_OPEN or self._failures >= self._threshold:
            self._state = self._OPEN
            self._opened_at = self._clock()
            self._failures = 0
            log.warning("cdse.breaker.open", reset_timeout_s=self._reset_timeout_s)

    def _refuse(self) -> CircuitOpenError:
        remaining = max(0.0, self._reset_timeout_s - (self._clock() - self._opened_at))
        return CircuitOpenError(f"CDSE circuit open; retrying in ~{remaining:.0f}s")

    async def call(self, fn: Callable[..., Awaitable[Any]], /, *args: Any, **kwargs: Any) -> Any:
        """Run an async call under the breaker: refuse fast when open, record the final outcome."""
        if not self.allow():
            raise self._refuse()
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def call_sync(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """The sync mirror of `call`, for the rasterio read path."""
        if not self.allow():
            raise self._refuse()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


def async_bucket_from_settings(settings: Settings) -> AsyncTokenBucket | None:
    """The shared CDSE bucket for the async clients, or None while no rate is configured - the
    code default, since the rate belongs to the deployed account and lives in env (see config).
    Building a redis client here is lazy - no connection happens until the first acquire."""
    if not settings.cdse_rate_limit_rps:
        return None
    import redis.asyncio as aredis

    client = aredis.from_url(settings.redis_url)
    return AsyncTokenBucket(
        client, capacity=settings.cdse_rate_limit_burst, rate_per_s=settings.cdse_rate_limit_rps
    )


def sync_bucket_from_settings(settings: Settings) -> SyncTokenBucket | None:
    """The same budget for the sync rasterio path, or None while no rate is configured."""
    if not settings.cdse_rate_limit_rps:
        return None
    import redis

    client = redis.from_url(settings.redis_url)
    return SyncTokenBucket(
        client, capacity=settings.cdse_rate_limit_burst, rate_per_s=settings.cdse_rate_limit_rps
    )
