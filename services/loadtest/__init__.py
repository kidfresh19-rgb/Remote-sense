"""Load-test harness (S1.3): a small async load driver plus pure latency/SLO evaluation. The SLO
numbers were set from the S4.7 load-test run against real data (PRD 0001, R12/R15). The
measurement core is infra-free and unit-tested; the runner takes an injected async operation so
it drives either a synthetic coroutine or a real HTTP call."""

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
