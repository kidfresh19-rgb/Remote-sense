"""Collection-planning tests (Phase 3): backfill window, dedup + gap detection (R-1), the
forward-fill cadence check, and the idempotency key. Pure logic, no infrastructure."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from services.worker.planning import (
    SENTINEL2_REVISIT_DAYS,
    backfill_window,
    collection_key,
    due_for_forward_fill,
    field_collection_key,
    plan_scenes,
)


def test_backfill_window_subtracts_calendar_months() -> None:
    start, end = backfill_window(date(2026, 5, 31), 18)
    assert end == date(2026, 5, 31)
    assert start == date(2024, 11, 30)  # day clamped to November's length


def test_backfill_window_rejects_nonpositive() -> None:
    with pytest.raises(ValueError, match="positive"):
        backfill_window(date(2026, 5, 31), 0)


def test_plan_scenes_skips_processed() -> None:
    discovered = ["a", "b", "c", "d"]
    assert plan_scenes(discovered, {"b", "d"}) == ["a", "c"]


def test_plan_scenes_preserves_order_and_dedups_input() -> None:
    discovered = ["a", "a", "b", "a", "c"]
    assert plan_scenes(discovered, set()) == ["a", "b", "c"]


def test_plan_scenes_all_processed_is_empty() -> None:
    assert plan_scenes(["a", "b"], {"a", "b"}) == []


def test_due_for_forward_fill_never_polled() -> None:
    assert due_for_forward_fill(None, datetime(2026, 5, 31, tzinfo=UTC)) is True


def test_due_for_forward_fill_recent_is_not_due() -> None:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    last = now - timedelta(days=2)
    assert due_for_forward_fill(last, now) is False


def test_due_for_forward_fill_after_cadence() -> None:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    last = now - timedelta(days=SENTINEL2_REVISIT_DAYS)
    assert due_for_forward_fill(last, now) is True


def test_collection_key_includes_geometry_version() -> None:
    key = collection_key("field-1", "S2_MOCK_X", 3)
    assert key == "collect:field-1:S2_MOCK_X:v3"


def test_field_collection_key_is_field_and_version_scoped() -> None:
    assert field_collection_key("field-1", 2) == "collect:field:field-1:v2"
    # A boundary change is a different unit of work, so the key changes with the version.
    assert field_collection_key("field-1", 2) != field_collection_key("field-1", 3)
    # And it never collides with the per-scene key (different lock granularity).
    assert field_collection_key("field-1", 2) != collection_key("field-1", "S2_X", 2)
