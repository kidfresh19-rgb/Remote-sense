"""No-DB unit tests for the analysis upsert statement (D3): an engine result maps onto the right
columns, and the upsert targets the analysis identity constraint so storage is idempotent and
additive. The live round-trip against PostGIS lives in test_analysis_db.py (skips without a DB)."""

from __future__ import annotations

import uuid
from datetime import date

from rs_core.repositories import _ANALYSIS_MUTABLE, _analysis_upsert_stmt, advance_cursor
from sqlalchemy.dialects import postgresql


def _values() -> dict[str, object]:
    return {
        "field_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "scene_id": "S2B_MSIL2A_20241015",
        "pass_date": date(2024, 10, 15),
        "index_name": "ndvi",
        "formula_version": "ndvi/v1",
        "geometry_version": 2,
        "provider": "cdse",
        "provider_scene_id": "S2B_MSIL2A_20241015",
        "processing_mode": "windowed_cog",
        "resolution_m": 10.0,
        "clear_fraction": 0.91,
        "mean": 0.62,
        "min_val": 0.10,
        "max_val": 0.85,
        "std": 0.07,
        "p10": 0.33,
        "p90": 0.78,
        "confidence": "high",
        "cog_uri": None,
    }


def _compiled():
    return _analysis_upsert_stmt(_values()).compile(dialect=postgresql.dialect())


def test_upsert_targets_identity_constraint_with_do_update() -> None:
    sql = str(_compiled()).lower()
    assert "insert into analysis" in sql
    assert "on conflict on constraint uq_analysis_identity" in sql
    assert "do update" in sql
    # created/updated detection; system columns (xmax) are unavailable through a partitioned
    # parent, so the idiom compares the insert-only created_at with the transaction timestamp.
    assert "(created_at = now())" in sql
    assert "xmax" not in sql


def test_upsert_carries_every_value_as_a_bind_param() -> None:
    params = _compiled().params
    for key, value in _values().items():
        assert params[key] == value


def test_upsert_refreshes_payload_but_never_the_identity() -> None:
    # DO UPDATE SET must refresh the computed payload + provenance, never the identity keys (they
    # define the row), the partition key (an ON CONFLICT update may not move a row across
    # partitions), nor the surrogate id / created_at.
    identity = {"field_id", "scene_id", "index_name", "geometry_version", "formula_version"}
    assert identity.isdisjoint(_ANALYSIS_MUTABLE)
    assert "mean" in _ANALYSIS_MUTABLE
    assert "clear_fraction" in _ANALYSIS_MUTABLE
    assert "pass_date" not in _ANALYSIS_MUTABLE
    assert "id" not in _ANALYSIS_MUTABLE
    assert "created_at" not in _ANALYSIS_MUTABLE


def test_advance_cursor_only_moves_forward() -> None:
    early = date(2024, 10, 1)
    late = date(2024, 10, 15)
    assert advance_cursor(None, None) is None
    assert advance_cursor(None, early) == early  # first observation seeds the cursor
    assert advance_cursor(early, None) == early  # a poll that found nothing leaves it put
    assert advance_cursor(early, late) == late  # advances to a newer pass
    assert advance_cursor(late, early) == late  # a late-arriving older scene never rewinds it
    assert advance_cursor(early, early) == early
