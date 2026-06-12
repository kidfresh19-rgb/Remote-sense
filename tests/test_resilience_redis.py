"""Lua-vs-Python parity for the CDSE token bucket (S4.5) against a real Redis: the pure
`refill_and_consume` is the reference, the Lua script is its atomic server-side deployment, and
this pins the two together so they can never drift. Needs Redis, so it SKIPS when no server is
reachable and runs for real in CI / `docker compose`."""

from __future__ import annotations

import os
import uuid

import pytest
from rs_imagery.resilience import _CONSUME_LUA, SyncTokenBucket, refill_and_consume

_REDIS_URL = os.environ.get("RS_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def redis_client():
    redis = pytest.importorskip("redis")
    client = redis.from_url(
        _REDIS_URL, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
    )
    try:
        client.ping()
    except Exception as exc:  # no Redis reachable -> skip
        pytest.skip(f"Redis not reachable at {_REDIS_URL}: {exc}")
    yield client
    client.close()


def test_lua_matches_the_python_reference(redis_client) -> None:
    key = f"test:quota:{uuid.uuid4().hex}"
    capacity, rate = 5.0, 2.0
    # A scenario walking through burst drain, deny, partial refill, and cap: fixed timestamps so
    # both implementations see the exact same clock.
    steps_ms = [1_000, 1_000, 1_000, 1_000, 1_000, 1_000, 1_400, 2_000, 3_600_000]

    tokens, ts = capacity, steps_ms[0]
    try:
        for now_ms in steps_ms:
            allowed_lua, retry_ms_lua = redis_client.eval(
                _CONSUME_LUA, 1, key, capacity, rate, now_ms, 1.0
            )
            tokens, ts, allowed_py, retry_s_py = refill_and_consume(
                tokens, ts, now_ms, capacity=capacity, rate_per_s=rate, cost=1.0
            )
            assert bool(allowed_lua) == allowed_py, f"diverged at t={now_ms}"
            assert int(retry_ms_lua) == pytest.approx(retry_s_py * 1000, abs=1), (
                f"retry hint diverged at t={now_ms}"
            )
            # The stored state must track the reference too, not just the verdicts.
            stored = redis_client.hget(key, "t")
            assert float(stored) == pytest.approx(tokens, abs=1e-6)
    finally:
        redis_client.delete(key)


def test_sync_bucket_round_trips_against_real_redis(redis_client) -> None:
    key = f"test:quota:{uuid.uuid4().hex}"
    waits: list[float] = []
    bucket = SyncTokenBucket(
        redis_client,
        capacity=2.0,
        rate_per_s=100.0,  # fast refill so the waiting branch stays sub-second
        key=key,
        sleep=lambda s: waits.append(s),
    )
    try:
        bucket.acquire()
        bucket.acquire()  # drains the burst
        bucket.acquire()  # third token: granted after at most a few ~10 ms waits
        assert waits, "the drained bucket should have made the third acquire wait"
        assert all(0 < w <= 0.011 for w in waits)
    finally:
        redis_client.delete(key)
