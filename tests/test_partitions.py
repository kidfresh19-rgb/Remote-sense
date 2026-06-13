"""Partition planning math (S4.1): the rolling monthly window the analysis table keeps around
its writes. Pure - no DB here; the end-to-end DDL run lives in test_partitions_db.py."""

from __future__ import annotations

from datetime import date

import pytest

from services.worker.partitions import (
    DEFAULT_LOOKAHEAD_MONTHS,
    add_months,
    month_floor,
    partition_name,
    plan_partition_months,
)


def test_month_floor_pins_first_day() -> None:
    assert month_floor(date(2026, 6, 12)) == date(2026, 6, 1)
    assert month_floor(date(2026, 6, 1)) == date(2026, 6, 1)


def test_add_months_wraps_years_in_both_directions() -> None:
    assert add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert add_months(date(2026, 1, 1), -2) == date(2025, 11, 1)
    assert add_months(date(2026, 6, 1), 0) == date(2026, 6, 1)


def test_partition_name_zero_pads_for_chronological_sort() -> None:
    assert partition_name(date(2026, 6, 1)) == "analysis_y2026m06"
    assert partition_name(date(2026, 11, 1)) == "analysis_y2026m11"
    # Lexicographic order must equal chronological order.
    assert partition_name(date(2026, 9, 1)) < partition_name(date(2026, 10, 1))


def test_plan_spans_slack_behind_horizon_to_lookahead_ahead() -> None:
    months = plan_partition_months(date(2026, 6, 12), backfill_months=18)
    # Horizon floor 2024-12-12 -> month 2024-12, minus one slack month -> 2024-11.
    assert months[0] == date(2024, 11, 1)
    # Default lookahead: three months past today's month.
    assert DEFAULT_LOOKAHEAD_MONTHS == 3
    assert months[-1] == date(2026, 9, 1)
    assert len(months) == 23


def test_plan_is_contiguous_ascending_month_starts() -> None:
    months = plan_partition_months(date(2026, 6, 12), backfill_months=6, lookahead_months=1)
    assert all(m.day == 1 for m in months)
    assert all(add_months(a, 1) == b for a, b in zip(months, months[1:], strict=False))


def test_plan_zero_lookahead_ends_in_the_current_month() -> None:
    months = plan_partition_months(date(2026, 6, 12), backfill_months=1, lookahead_months=0)
    assert months[-1] == date(2026, 6, 1)


def test_plan_rejects_negative_lookahead() -> None:
    with pytest.raises(ValueError):
        plan_partition_months(date(2026, 6, 12), backfill_months=18, lookahead_months=-1)


def test_plan_rejects_nonpositive_backfill() -> None:
    # Delegated to backfill_window so the partition window and the advertised history depth
    # share one validation.
    with pytest.raises(ValueError):
        plan_partition_months(date(2026, 6, 12), backfill_months=0)
