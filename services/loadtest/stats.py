"""Pure latency and throughput statistics for the load-test harness (S1.3). No I/O and no infra:
it turns a list of per-call latency samples into summary metrics, so the harness's reporting is
unit-testable with synthetic timings and the runner stays separable from the measurement."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyStats:
    """Summary of one load run: the latency distribution of the successful calls, the failure
    count, and the observed throughput over the measured wall-clock window."""

    count: int
    failures: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    throughput_rps: float


def percentile_ms(sorted_ms: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile of an ascending-sorted latency list. Nearest-rank (not
    interpolated) is chosen for a deterministic, explainable SLO check: the p95 of 1..100 is 95.
    The caller passes an already-sorted sequence so a multi-percentile summary sorts once."""
    if not sorted_ms:
        raise ValueError("percentile of no samples")
    if not 0 < pct <= 100:
        raise ValueError("pct must be in (0, 100]")
    rank = math.ceil(pct / 100 * len(sorted_ms))
    return sorted_ms[rank - 1]


def summarize(
    latencies_ms: Sequence[float],
    *,
    failures: int = 0,
    wall_clock_s: float,
) -> LatencyStats:
    """Summarize successful per-call latencies plus a failure count over a measured wall-clock
    window. Throughput is completed calls over wall-clock (the harness's observed rate), not the
    inverse of mean latency, so concurrency is reflected honestly. With no successful calls the
    latency fields are zero and only the failure count and throughput carry signal."""
    if wall_clock_s <= 0:
        raise ValueError("wall_clock_s must be positive")
    count = len(latencies_ms)
    if count == 0:
        return LatencyStats(0, failures, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    ordered = sorted(latencies_ms)
    return LatencyStats(
        count=count,
        failures=failures,
        mean_ms=sum(ordered) / count,
        p50_ms=percentile_ms(ordered, 50),
        p95_ms=percentile_ms(ordered, 95),
        p99_ms=percentile_ms(ordered, 99),
        max_ms=ordered[-1],
        throughput_rps=count / wall_clock_s,
    )
