"""The three scenarios the load-test harness ships with (S1.3): tile latency, per-field analysis
time, and backfill throughput (PRD 0001, R12/R15). Each is a set of SLO targets paired at run
time with an operation that hits the corresponding endpoint. The thresholds are PLACEHOLDERS,
flagged for confirmation, until S4.7 sets real numbers from a representative load test."""

from __future__ import annotations

import httpx

from services.loadtest.runner import Operation
from services.loadtest.slo import Metric, SLOTarget

# ⚑ CONFIRM (S4.7): the SLO numbers below are engineering placeholders, not agreed targets. S4.7
# replaces them with measurements against representative farm and scene volumes (R12).
TILE_LATENCY_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("tile p95 latency", Metric.P95_MS, 400.0, upper_bound=True),
    SLOTarget("tile p99 latency", Metric.P99_MS, 800.0, upper_bound=True),
    SLOTarget("tile errors", Metric.FAILURES, 0.0, upper_bound=True),
)
ANALYSIS_TIME_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("per-field analysis p95", Metric.P95_MS, 30_000.0, upper_bound=True),
    SLOTarget("analysis errors", Metric.FAILURES, 0.0, upper_bound=True),
)
BACKFILL_THROUGHPUT_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("backfill enqueue throughput", Metric.THROUGHPUT_RPS, 20.0, upper_bound=False),
    SLOTarget("backfill errors", Metric.FAILURES, 0.0, upper_bound=True),
)

SLO_SETS: dict[str, tuple[SLOTarget, ...]] = {
    "tile": TILE_LATENCY_SLOS,
    "analysis": ANALYSIS_TIME_SLOS,
    "backfill": BACKFILL_THROUGHPUT_SLOS,
}


def http_get_op(client: httpx.AsyncClient, url: str) -> Operation:
    """An operation that GETs `url` and treats any non-2xx as a failure via `raise_for_status`."""

    async def _op() -> None:
        response = await client.get(url)
        response.raise_for_status()

    return _op


def http_post_op(
    client: httpx.AsyncClient, url: str, json: dict[str, object] | None = None
) -> Operation:
    """An operation that POSTs `url` (optionally with a JSON body) and treats any non-2xx as a
    failure via `raise_for_status`."""

    async def _op() -> None:
        response = await client.post(url, json=json)
        response.raise_for_status()

    return _op
