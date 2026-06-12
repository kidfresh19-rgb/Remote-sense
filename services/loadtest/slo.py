"""SLO targets for the load-test harness (S1.3) and the pass/fail evaluation of a measured run
against them. The pairing is pure so it is unit-testable; the actual threshold values live with
the scenarios that own them (see scenarios.py) and are placeholders until S4.7 sets real numbers
from a representative load test (PRD 0001, R12)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from services.loadtest.stats import LatencyStats


class Metric(StrEnum):
    """A field of `LatencyStats` an SLO can target. The value matches the dataclass attribute so
    evaluation can read it by name."""

    P50_MS = "p50_ms"
    P95_MS = "p95_ms"
    P99_MS = "p99_ms"
    THROUGHPUT_RPS = "throughput_rps"
    FAILURES = "failures"


@dataclass(frozen=True)
class SLOTarget:
    """One service-level objective: a metric, the threshold it must hold, and whether the metric
    is an upper bound (latency, error count: measured <= threshold) or a lower bound (throughput:
    measured >= threshold)."""

    name: str
    metric: Metric
    threshold: float
    upper_bound: bool


@dataclass(frozen=True)
class SLOResult:
    target: SLOTarget
    measured: float
    passed: bool


@dataclass(frozen=True)
class SLOReport:
    scenario: str
    results: tuple[SLOResult, ...]

    @property
    def passed(self) -> bool:
        """The run meets its objectives only if every target passed."""
        return all(result.passed for result in self.results)


def evaluate(scenario: str, stats: LatencyStats, targets: Sequence[SLOTarget]) -> SLOReport:
    """Check measured stats against each SLO target. Upper-bound targets pass when measured is at
    or below the threshold; lower-bound targets pass when measured is at or above it."""
    results = []
    for target in targets:
        measured = float(getattr(stats, target.metric.value))
        passed = (
            measured <= target.threshold if target.upper_bound else measured >= target.threshold
        )
        results.append(SLOResult(target, measured, passed))
    return SLOReport(scenario, tuple(results))
