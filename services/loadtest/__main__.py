"""CLI for the S1.3 load-test harness: `python -m services.loadtest --url /healthz --slo-set tile`.
It drives one scenario against a running stack and prints the SLO report, exiting non-zero if any
SLO fails so it can gate a pipeline once S4.7 sets real thresholds. Until then the thresholds are
placeholders (see scenarios.py). Intended targets per set: tile -> the tiler tile route, analysis
-> a per-field analyse trigger, backfill -> a backfill-enqueue endpoint."""

from __future__ import annotations

import argparse
import asyncio

import httpx

from services.loadtest.runner import run_load
from services.loadtest.scenarios import SLO_SETS, http_get_op, http_post_op
from services.loadtest.slo import SLOReport, evaluate
from services.loadtest.stats import summarize


def format_report(report: SLOReport) -> str:
    lines = [f"SLO report [{report.scenario}]: {'PASS' if report.passed else 'FAIL'}"]
    for result in report.results:
        bound = "<=" if result.target.upper_bound else ">="
        marker = "ok  " if result.passed else "FAIL"
        lines.append(
            f"  [{marker}] {result.target.name}: "
            f"{result.measured:.1f} {bound} {result.target.threshold:.1f}"
        )
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> SLOReport:
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=httpx.Timeout(args.timeout_s)
    ) as client:
        operation = (
            http_post_op(client, args.url)
            if args.method == "POST"
            else http_get_op(client, args.url)
        )
        result = await run_load(operation, total=args.total, concurrency=args.concurrency)
    stats = summarize(
        result.latencies_ms, failures=result.failures, wall_clock_s=result.wall_clock_s
    )
    return evaluate(args.slo_set, stats, SLO_SETS[args.slo_set])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.loadtest", description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--url", required=True, help="path to hit, e.g. /healthz")
    parser.add_argument("--method", choices=("GET", "POST"), default="GET")
    parser.add_argument("--slo-set", choices=tuple(SLO_SETS), default="tile")
    parser.add_argument("--total", type=int, default=200, help="total calls to issue")
    parser.add_argument("--concurrency", type=int, default=10, help="max calls in flight")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    args = parser.parse_args(argv)
    report = asyncio.run(run(args))
    print(format_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
