"""Load-test harness (S1.3): a small async load driver plus pure latency/SLO evaluation, so the
team can stand up load tests with placeholder SLOs now and set the real numbers in S4.7
(PRD 0001, R12/R15). The measurement core is infra-free and unit-tested; the runner takes an
injected async operation so it drives either a synthetic coroutine or a real HTTP call."""

from __future__ import annotations

from services.loadtest.runner import RunResult, run_load
from services.loadtest.slo import Metric, SLOReport, SLOResult, SLOTarget, evaluate
from services.loadtest.stats import LatencyStats, percentile_ms, summarize

__all__ = [
    "LatencyStats",
    "Metric",
    "RunResult",
    "SLOReport",
    "SLOResult",
    "SLOTarget",
    "evaluate",
    "percentile_ms",
    "run_load",
    "summarize",
]
