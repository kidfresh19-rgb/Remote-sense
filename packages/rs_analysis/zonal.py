"""Zonal statistics over a field polygon. Masked pixels (cloud/shadow/NoData via SCL, plus
anything NaN) are excluded before any statistic is computed (PLAN §5, rule 5). The result is
index-agnostic; the same shape is stored for every index."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ZonalStats:
    """Per-field, per-pass summary. `count` is the number of valid pixels the stats are based
    on; when it is 0 every statistic is None (nothing usable to summarise)."""

    count: int
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    std: float | None = None
    p5: float | None = None
    p10: float | None = None
    p90: float | None = None
    p95: float | None = None


def zonal_stats(values: np.ndarray, include: np.ndarray | None = None) -> ZonalStats:
    """Summarise `values` over the included pixels. `include` is a boolean mask (True = use
    this pixel) combining the AOI footprint and the SCL clear mask; without it the whole array
    is considered. Non-finite values (NaN/inf) are always excluded."""
    arr = np.asarray(values, dtype="float64")
    selected = arr if include is None else arr[include]
    valid = selected[np.isfinite(selected)]
    if valid.size == 0:
        return ZonalStats(count=0)
    p5, p10, p90, p95 = np.percentile(valid, [5, 10, 90, 95])
    return ZonalStats(
        count=int(valid.size),
        mean=float(np.mean(valid)),
        min=float(np.min(valid)),
        max=float(np.max(valid)),
        std=float(np.std(valid)),
        p5=float(p5),
        p10=float(p10),
        p90=float(p90),
        p95=float(p95),
    )
