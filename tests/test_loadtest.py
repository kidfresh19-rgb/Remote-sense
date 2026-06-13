"""Unit tests for the S1.3 load-test harness. No network, no DB: the runner is driven with an
in-process coroutine and the HTTP op factories use httpx's MockTransport, so the measurement,
SLO, and operation logic are all verified without a running stack."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from services.loadtest.runner import run_load
from services.loadtest.scenarios import http_get_op
from services.loadtest.slo import Metric, SLOTarget, evaluate
from services.loadtest.stats import LatencyStats, percentile_ms, summarize


def test_percentile_nearest_rank() -> None:
    ordered = [float(i) for i in range(1, 101)]  # 1..100
    assert percentile_ms(ordered, 50) == 50
    assert percentile_ms(ordered, 95) == 95
    assert percentile_ms(ordered, 99) == 99
    assert percentile_ms(ordered, 100) == 100


def test_percentile_rejects_empty_and_bad_pct() -> None:
    with pytest.raises(ValueError):
        percentile_ms([], 95)
    with pytest.raises(ValueError):
        percentile_ms([1.0], 0)
    with pytest.raises(ValueError):
        percentile_ms([1.0], 150)


def test_summarize_basic() -> None:
    stats = summarize([10.0, 20.0, 30.0, 40.0], failures=1, wall_clock_s=2.0)
    assert stats.count == 4
    assert stats.failures == 1
    assert stats.mean_ms == 25.0
    assert stats.max_ms == 40.0
    assert stats.p50_ms == 20.0  # nearest-rank: ceil(0.5*4)=2 -> index 1 -> 20
    assert stats.throughput_rps == 2.0  # 4 completed / 2s wall clock


def test_summarize_all_failed() -> None:
    stats = summarize([], failures=5, wall_clock_s=1.0)
    assert stats.count == 0
    assert stats.failures == 5
    assert stats.p95_ms == 0.0
    assert stats.throughput_rps == 0.0


def test_summarize_rejects_nonpositive_wall_clock() -> None:
    with pytest.raises(ValueError):
        summarize([1.0], wall_clock_s=0.0)


def test_evaluate_upper_and_lower_bounds() -> None:
    stats = LatencyStats(
        count=10,
        failures=0,
        mean_ms=100.0,
        p50_ms=90.0,
        p95_ms=180.0,
        p99_ms=300.0,
        max_ms=350.0,
        throughput_rps=25.0,
    )
    targets = [
        SLOTarget("p95", Metric.P95_MS, 200.0, upper_bound=True),  # 180 <= 200 -> pass
        SLOTarget("p99", Metric.P99_MS, 250.0, upper_bound=True),  # 300 <= 250 -> fail
        SLOTarget("rps", Metric.THROUGHPUT_RPS, 20.0, upper_bound=False),  # 25 >= 20 -> pass
        SLOTarget("errors", Metric.FAILURES, 0.0, upper_bound=True),  # 0 <= 0 -> pass
    ]
    report = evaluate("demo", stats, targets)
    assert [r.passed for r in report.results] == [True, False, True, True]
    assert report.passed is False


async def test_run_load_collects_samples_and_respects_concurrency() -> None:
    in_flight = 0
    peak = 0

    async def op() -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.005)
        in_flight -= 1

    result = await run_load(op, total=20, concurrency=4)
    assert len(result.latencies_ms) == 20
    assert result.failures == 0
    assert peak <= 4
    assert result.wall_clock_s > 0


async def test_run_load_counts_failures_not_latency() -> None:
    async def flaky() -> None:
        raise RuntimeError("boom")

    result = await run_load(flaky, total=5, concurrency=2)
    assert result.failures == 5
    assert result.latencies_ms == []


async def test_run_load_validates_args() -> None:
    async def op() -> None:
        return None

    with pytest.raises(ValueError):
        await run_load(op, total=0, concurrency=1)
    with pytest.raises(ValueError):
        await run_load(op, total=1, concurrency=0)


async def test_http_get_op_records_success_and_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok") if request.url.path == "/ok" else httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await run_load(http_get_op(client, "/ok"), total=3, concurrency=2)
        assert ok.failures == 0
        assert len(ok.latencies_ms) == 3

        bad = await run_load(http_get_op(client, "/down"), total=3, concurrency=2)
        assert bad.failures == 3
        assert bad.latencies_ms == []


def test_parse_headers_builds_a_header_dict() -> None:
    from services.loadtest.__main__ import parse_headers

    parsed = parse_headers(["Authorization: Bearer abc", "X-Extra:  spaced  "])
    assert parsed == {"Authorization": "Bearer abc", "X-Extra": "spaced"}
    assert parse_headers([]) == {}


def test_parse_headers_rejects_malformed_pairs() -> None:
    from services.loadtest.__main__ import parse_headers

    with pytest.raises(SystemExit):
        parse_headers(["no-colon-here"])
    with pytest.raises(SystemExit):
        parse_headers([": value-without-name"])
