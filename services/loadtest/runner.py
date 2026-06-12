"""The async load runner for the harness (S1.3). It drives a caller-supplied async operation at a
target concurrency for a fixed number of iterations and times each call. The operation is
injected, so the measurement is testable with a synthetic in-process coroutine (no network, no
server) and the same runner drives real HTTP calls in a deployed scenario."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

Operation = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class RunResult:
    """Raw output of one load run: the successful per-call latencies (ms), the count of calls that
    raised, and the wall-clock window the calls spanned."""

    latencies_ms: list[float]
    failures: int
    wall_clock_s: float


async def run_load(operation: Operation, *, total: int, concurrency: int) -> RunResult:
    """Run `operation` `total` times with at most `concurrency` in flight, timing each call. A
    raised exception counts as a failure and is not timed into the latency distribution, so an
    endpoint that errors under load shows up as failures rather than as deceptively fast calls.
    The shared list and counter need no lock: asyncio coroutines yield only at `await`, and
    neither the append nor the increment awaits."""
    if total <= 0:
        raise ValueError("total must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")

    semaphore = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    failures = 0

    async def one() -> None:
        nonlocal failures
        async with semaphore:
            start = time.perf_counter()
            try:
                await operation()
            except Exception:
                failures += 1
                return
            latencies_ms.append((time.perf_counter() - start) * 1000)

    wall_start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(total)))
    wall_clock_s = time.perf_counter() - wall_start
    return RunResult(latencies_ms, failures, wall_clock_s)
