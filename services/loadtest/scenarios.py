"""The load-test scenarios (S1.3) with the measured SLO numbers (S4.7): tile latency, per-field
analysis time, backfill enqueue throughput, and farm-ingest data throughput (PRD 0001, R12/R15).
Each is a set of SLO targets paired at run time with an operation that hits the corresponding
endpoint.

The thresholds were set 2026-06-12 from runs against the dev compose stack on real data (9
farms' live CDSE history, 858 analysed passes); the measurement record, exact commands, and the
derivation of every number live in docs/plan/S4.7-slo-numbers.md. Policy: latency thresholds sit
at roughly 2x the measured p95/p99 and throughput floors at roughly half the measured rate, so a
pass is stable across machine variance while a regression past 2x still fails the gate."""

from __future__ import annotations

import httpx

from services.loadtest.runner import Operation
from services.loadtest.slo import Metric, SLOTarget

# Measured: p95 177.7 ms / p99 181.5 ms, 0 errors (hot single-tile render through nginx ->
# tiler -> MinIO COG, n=200, concurrency 10).
TILE_LATENCY_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("tile p95 latency", Metric.P95_MS, 400.0, upper_bound=True),
    SLOTarget("tile p99 latency", Metric.P99_MS, 800.0, upper_bound=True),
    SLOTarget("tile errors", Metric.FAILURES, 0.0, upper_bound=True),
)
# Derived from pipeline history, not an HTTP run (each trigger costs real CDSE quota, so this
# set is for a staging soak only): one forward-fill pass is ~10 governed CDSE requests (~2.5 s
# of quota wait at 4 rps) plus transfer and compute; the worst sustained backfill ran at
# 35 s/pass effective. 60 s p95 covers the single-pass tail with CDSE variance.
ANALYSIS_TIME_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("per-field analysis p95", Metric.P95_MS, 60_000.0, upper_bound=True),
    SLOTarget("analysis errors", Metric.FAILURES, 0.0, upper_bound=True),
)
# Measured: 251.1 rps accepted, 0 errors (authenticated POST /fields/{id}/collect with the
# worker stopped so every call is a pure enqueue; queue purged afterwards).
BACKFILL_THROUGHPUT_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("backfill enqueue throughput", Metric.THROUGHPUT_RPS, 100.0, upper_bound=False),
    SLOTarget("backfill errors", Metric.FAILURES, 0.0, upper_bound=True),
)
# Measured: 89.5 rps / p95 213.5 ms, 0 errors (POST /ingest/farm replaying a real farm payload,
# the idempotent gateway re-push path). The R16 data-in proof: at the 40 rps floor, 250k farms
# re-ingest in under two hours.
INGEST_THROUGHPUT_SLOS: tuple[SLOTarget, ...] = (
    SLOTarget("farm ingest throughput", Metric.THROUGHPUT_RPS, 40.0, upper_bound=False),
    SLOTarget("farm ingest p95 latency", Metric.P95_MS, 500.0, upper_bound=True),
    SLOTarget("ingest errors", Metric.FAILURES, 0.0, upper_bound=True),
)

SLO_SETS: dict[str, tuple[SLOTarget, ...]] = {
    "tile": TILE_LATENCY_SLOS,
    "analysis": ANALYSIS_TIME_SLOS,
    "backfill": BACKFILL_THROUGHPUT_SLOS,
    "ingest": INGEST_THROUGHPUT_SLOS,
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
