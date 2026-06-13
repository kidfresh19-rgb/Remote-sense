"""Unit tests for the COG retention policy (S4.3, risk S-1) - the pure decision logic only.
No DB, no object store: rows go in as tuples, prune decisions come out. The orchestration
against PostGIS + a fake store is covered in test_retention_db.py."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from services.worker.retention import CogRow, retention_cutoff, select_prunable

_FIELD_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
_FIELD_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")


def _row(
    field_id: uuid.UUID,
    geometry_version: int,
    pass_date: date,
    key: str,
) -> CogRow:
    return CogRow(
        analysis_id=uuid.uuid4(),
        field_id=field_id,
        geometry_version=geometry_version,
        pass_date=pass_date,
        cog_key=key,
    )


def test_retention_cutoff_matches_backfill_window_start() -> None:
    # The default horizon mirrors the advertised history depth: 18 months back from today.
    assert retention_cutoff(date(2026, 6, 12), 18) == date(2024, 12, 12)
    # Calendar-correct clamping, same as the backfill window.
    assert retention_cutoff(date(2026, 3, 31), 1) == date(2026, 2, 28)


def test_retention_cutoff_rejects_nonpositive_months() -> None:
    with pytest.raises(ValueError):
        retention_cutoff(date(2026, 6, 12), 0)


def test_stale_geometry_version_pruned_even_when_recent() -> None:
    recent = date(2026, 6, 1)
    rows = [_row(_FIELD_A, 1, recent, "cog/v1/a/s1/ndvi.tif")]
    pruned = select_prunable(rows, {_FIELD_A: 2}, cutoff=date(2024, 12, 12))
    assert [r.cog_key for r in pruned] == ["cog/v1/a/s1/ndvi.tif"]


def test_aged_out_pass_pruned_at_current_geometry_version() -> None:
    rows = [_row(_FIELD_A, 2, date(2024, 1, 5), "cog/v2/a/s0/ndvi.tif")]
    pruned = select_prunable(rows, {_FIELD_A: 2}, cutoff=date(2024, 12, 12))
    assert [r.cog_key for r in pruned] == ["cog/v2/a/s0/ndvi.tif"]


def test_recent_pass_at_current_geometry_version_kept() -> None:
    cutoff = date(2024, 12, 12)
    rows = [
        _row(_FIELD_A, 2, date(2026, 6, 1), "cog/v2/a/s1/ndvi.tif"),
        _row(_FIELD_A, 2, cutoff, "cog/v2/a/s2/ndvi.tif"),  # exactly at the cutoff: kept
    ]
    assert select_prunable(rows, {_FIELD_A: 2}, cutoff=cutoff) == []


def test_fields_evaluated_independently() -> None:
    cutoff = date(2024, 12, 12)
    rows = [
        _row(_FIELD_A, 1, date(2026, 6, 1), "cog/v1/a/s1/ndvi.tif"),  # stale gv on A
        _row(_FIELD_B, 1, date(2026, 6, 1), "cog/v1/b/s1/ndvi.tif"),  # current gv on B
    ]
    pruned = select_prunable(rows, {_FIELD_A: 2, _FIELD_B: 1}, cutoff=cutoff)
    assert [r.cog_key for r in pruned] == ["cog/v1/a/s1/ndvi.tif"]


def test_unknown_field_version_is_left_alone() -> None:
    # A row whose field is missing from the version map (deleted mid-query, say) is never
    # pruned on the stale-gv prong; only an aged-out pass date can prune it.
    rows = [_row(_FIELD_A, 1, date(2026, 6, 1), "cog/v1/a/s1/ndvi.tif")]
    assert select_prunable(rows, {}, cutoff=date(2024, 12, 12)) == []
