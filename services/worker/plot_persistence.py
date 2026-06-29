"""Map an AOI-series engine pass onto the per-plot analysis upsert (backlog 0031).

The worker is the layer allowed to know both the analysis engine (the pass-dict shape) and rs_core
(the store), so this mapping lives here, keeping rs_core free of an upward dependency - the same
boundary `persistence.py` keeps for field analyses. Pure: no DB, unit-testable on one pass."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, TypedDict

from rs_core.proxy_aoi import MIN_USABLE_PIXELS


class PlotAnalysisUpsertKwargs(TypedDict):
    """The exact keyword arguments `upsert_plot_analysis` accepts, typed so that `**`-unpacking the
    mapping into the call type-checks against its signature."""

    plot_id: uuid.UUID
    scene_id: str
    pass_date: date
    index_name: str
    formula_version: str
    provider: str
    provider_scene_id: str
    processing_mode: str
    resolution_m: float
    clear_fraction: float
    pixels: int
    low_pixel_quality: bool
    mean: float | None
    min_val: float | None
    max_val: float | None
    p10: float | None
    p90: float | None
    confidence: str | None


def plot_series_upsert_kwargs(
    pass_result: dict[str, Any], *, plot_id: uuid.UUID
) -> PlotAnalysisUpsertKwargs:
    """Flatten one `status="ok"` AOI-series pass dict (from `_analyse_aoi_series`) into the keyword
    arguments `upsert_plot_analysis` expects. The §4 `low_pixel_quality` flag is derived from the
    pass's clear-pixel `pixels` count against `MIN_USABLE_PIXELS`, so a 2-pixel plot (or a
    mostly-cloudy pass) is marked, never shown as a confident statistic. Pure - no DB."""
    pixels = int(pass_result.get("pixels") or 0)
    scene_id = str(pass_result.get("scene_id", "unknown"))
    return {
        "plot_id": plot_id,
        "scene_id": scene_id,
        "pass_date": date.fromisoformat(pass_result["pass_date"]),
        "index_name": pass_result["index"],
        "formula_version": str(pass_result.get("formula_version", "unknown")),
        "provider": str(pass_result.get("provider", "unknown")),
        "provider_scene_id": str(pass_result.get("provider_scene_id") or scene_id),
        "processing_mode": str(pass_result.get("processing_mode", "unknown")),
        "resolution_m": float(pass_result.get("resolution_m", 10)),
        "clear_fraction": float(pass_result.get("clear_fraction", 0.0)),
        "pixels": pixels,
        "low_pixel_quality": pixels < MIN_USABLE_PIXELS,
        "mean": pass_result.get("mean"),
        "min_val": pass_result.get("min"),
        "max_val": pass_result.get("max"),
        "p10": pass_result.get("p10"),
        "p90": pass_result.get("p90"),
        "confidence": pass_result.get("confidence"),
    }
