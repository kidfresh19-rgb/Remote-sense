"""CLI for the load-test harness: `python -m services.loadtest --url /healthz --slo-set tile`.
It drives one scenario against a running stack and prints the SLO report, exiting non-zero if
any SLO fails. Thresholds are the measured S4.7 numbers (see scenarios.py, with the measurement
record in docs/plan/S4.7-slo-numbers.md). Targets per set: tile -> the tiler tile route,
ingest -> POST /ingest/farm with a replayed payload (--json-file), backfill -> the
authenticated collect trigger (--header, with the worker stopped and the queue purged after),
analysis -> the collect trigger on a staging stack (each call costs real CDSE quota)."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib

import httpx

from services.loadtest.runner import run_load
from services.loadtest.scenarios import SLO_SETS, http_get_op, http_post_op
from services.loadtest.slo import SLOReport, evaluate
from services.loadtest.stats import summarize


def parse_headers(pairs: list[str]) -> dict[str, str]:
    """Parse repeated `--header "Name: value"` arguments; a pair without a colon is an error."""
    headers: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition(":")
        if not sep or not name.strip():
            raise SystemExit(f"--header expects 'Name: value', got {pair!r}")
        headers[name.strip()] = value.strip()
    return headers


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


async def run(args: argparse.Namespace, payload: dict[str, object] | None) -> SLOReport:
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(args.timeout_s),
        headers=parse_headers(args.header),
    ) as client:
        operation = (
            http_post_op(client, args.url, json=payload)
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
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME: VALUE",
        help="extra request header, repeatable (e.g. 'Authorization: Bearer ...')",
    )
    parser.add_argument(
        "--json-file",
        default=None,
        help="path to a JSON file sent as the POST body (ignored for GET)",
    )
    args = parser.parse_args(argv)
    # Read the body before entering the event loop; the operation itself must stay I/O-clean.
    payload: dict[str, object] | None = None
    if args.json_file is not None:
        payload = json.loads(pathlib.Path(args.json_file).read_text(encoding="utf-8"))
    report = asyncio.run(run(args, payload))
    print(format_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
