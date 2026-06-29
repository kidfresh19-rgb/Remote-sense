"""Pure unit tests for Ward Watch per-household ingestion (backlog 0031): the engine-pass -> upsert
mapping (incl. the §4 low-pixel flag), the clearest-per-day dedupe, the wire -> value mapping, and
the plot-analysis upsert statement builder - all with no database. The live PostGIS round-trip lives
in test_ward_watch_ingest_db.py (skips without a DB)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from rs_core.proxy_aoi import MIN_USABLE_PIXELS
from rs_core.repositories import _PLOT_ANALYSIS_MUTABLE, _plot_analysis_upsert_stmt
from rs_sync import (
    DeclaredCrop,
    HouseholdDeclaration,
    HouseholdDeclarationBatch,
    PlantingDeclaration,
    PlotDeclaration,
    synthetic_declarations,
)
from sqlalchemy.dialects import postgresql

from services.worker.plot_persistence import plot_series_upsert_kwargs
from services.worker.tasks.ward_watch import _clearest_per_day, _to_household_values


def _ok_pass(pass_date: str, *, clear: float, pixels: int, mean: float = 0.5) -> dict[str, Any]:
    return {
        "status": "ok",
        "index": "ndvi",
        "pass_date": pass_date,
        "scene_id": f"S2_{pass_date}",
        "formula_version": "ndvi-v1",
        "provider": "mock",
        "provider_scene_id": f"prov_{pass_date}",
        "processing_mode": "mock",
        "mean": mean,
        "min": 0.1,
        "max": 0.9,
        "p10": 0.2,
        "p90": 0.8,
        "clear_fraction": clear,
        "confidence": "high",
        "resolution_m": 10.0,
        "pixels": pixels,
    }


def test_plot_series_upsert_kwargs_maps_full_pass() -> None:
    plot_id = uuid.uuid4()
    kw = plot_series_upsert_kwargs(_ok_pass("2025-01-10", clear=0.9, pixels=50), plot_id=plot_id)
    assert kw["plot_id"] == plot_id
    assert kw["index_name"] == "ndvi"
    assert kw["pass_date"] == date(2025, 1, 10)
    assert kw["scene_id"] == "S2_2025-01-10"
    assert kw["provider"] == "mock"
    assert kw["provider_scene_id"] == "prov_2025-01-10"
    assert kw["processing_mode"] == "mock"
    assert kw["formula_version"] == "ndvi-v1"
    assert kw["clear_fraction"] == 0.9
    assert (kw["mean"], kw["min_val"], kw["max_val"]) == (0.5, 0.1, 0.9)
    assert kw["pixels"] == 50
    assert kw["low_pixel_quality"] is False


def test_plot_series_upsert_kwargs_flags_low_pixels() -> None:
    # A 2-pixel pass is below MIN_USABLE_PIXELS -> flagged, never shown as confident (§4).
    assert MIN_USABLE_PIXELS > 2
    kw = plot_series_upsert_kwargs(
        _ok_pass("2025-01-10", clear=0.9, pixels=2), plot_id=uuid.uuid4()
    )
    assert kw["pixels"] == 2
    assert kw["low_pixel_quality"] is True


def test_clearest_per_day_keeps_clearest_and_drops_non_ok() -> None:
    passes = [
        _ok_pass("2025-01-10", clear=0.4, pixels=40),
        _ok_pass("2025-01-10", clear=0.8, pixels=44),  # same day, clearer -> wins
        {"status": "no_pass", "pass_date": "2025-01-15"},
        {"status": "error", "detail": "boom", "pass_date": "2025-01-18"},
        _ok_pass("2025-01-20", clear=0.7, pixels=30),
    ]
    by_day = {p["pass_date"]: p for p in _clearest_per_day(passes)}
    assert set(by_day) == {"2025-01-10", "2025-01-20"}
    assert by_day["2025-01-10"]["clear_fraction"] == 0.8


def test_to_household_values_maps_synthetic_batch() -> None:
    by_id = {v.canonical_household_id: v for v in _to_household_values(synthetic_declarations())}
    assert set(by_id) == {"HH-1001", "HH-1002"}

    full = by_id["HH-1001"]
    assert full.client_uuid is not None
    crops = {(c.crop, c.weight_pct) for c in full.plots[0].crop_mix}
    assert crops == {("maize", 60.0), ("cowpea", 40.0)}
    assert full.plots[0].planting_date == date(2025, 11, 20)

    sparse = by_id["HH-1002"]
    assert sparse.plots[0].crop_mix == ()
    assert sparse.plots[0].planting_date is None


def test_to_household_values_is_tolerant_of_bad_ids_and_weightless_crops() -> None:
    batch = HouseholdDeclarationBatch(
        declarations=[
            HouseholdDeclaration(
                canonical_household_id="HH-Z",
                client_uuid="not-a-uuid",
                plots=[
                    PlotDeclaration(
                        client_uuid="also-bad",
                        crop_mix=[DeclaredCrop(crop="maize", weight_pct=None)],
                        planting=PlantingDeclaration(planting_date=None),
                    )
                ],
            )
        ]
    )
    value = _to_household_values(batch)[0]
    assert value.client_uuid is None
    assert value.plots[0].client_uuid is None
    assert value.plots[0].crop_mix == ()  # weightless crop dropped
    assert value.plots[0].planting_date is None


def _stmt_values() -> dict[str, Any]:
    return {
        "plot_id": uuid.uuid4(),
        "scene_id": "S2_x",
        "pass_date": date(2025, 1, 10),
        "index_name": "ndvi",
        "formula_version": "ndvi-v1",
        "provider": "mock",
        "provider_scene_id": "p",
        "processing_mode": "mock",
        "resolution_m": 10.0,
        "clear_fraction": 0.9,
        "pixels": 50,
        "low_pixel_quality": False,
        "mean": 0.5,
        "min_val": 0.1,
        "max_val": 0.9,
        "p10": 0.2,
        "p90": 0.8,
        "confidence": "high",
    }


def test_plot_analysis_upsert_targets_identity_constraint() -> None:
    sql = str(
        _plot_analysis_upsert_stmt(_stmt_values()).compile(dialect=postgresql.dialect())
    ).lower()
    assert "insert into plot_analysis" in sql
    assert "on conflict on constraint uq_plot_analysis_identity" in sql
    assert "do update" in sql


def test_plot_analysis_mutable_excludes_identity() -> None:
    identity = {"plot_id", "index_name", "pass_date", "formula_version"}
    assert identity.isdisjoint(_PLOT_ANALYSIS_MUTABLE)
    # the §4 flag, the clear fraction and the pixel count all refresh on a re-process
    assert {"low_pixel_quality", "clear_fraction", "pixels", "mean"} <= set(_PLOT_ANALYSIS_MUTABLE)
