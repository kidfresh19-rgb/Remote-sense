"""No-DB tests for the UTC/CAT time handling (S-4)."""

from __future__ import annotations

from datetime import UTC, datetime

from rs_core.timeutil import cat_range_to_utc, to_cat


def test_to_cat_shifts_utc_by_two_hours() -> None:
    cat = to_cat(datetime(2025, 1, 15, 8, 0, tzinfo=UTC))
    assert cat.hour == 10
    assert cat.utcoffset().total_seconds() == 7200


def test_to_cat_treats_naive_as_utc() -> None:
    assert to_cat(datetime(2025, 1, 15, 8, 0)).hour == 10


def test_cat_range_to_utc_subtracts_two_hours() -> None:
    start, end = cat_range_to_utc(datetime(2025, 1, 15, 10, 0), datetime(2025, 1, 16, 10, 0))
    assert start == datetime(2025, 1, 15, 8, 0, tzinfo=UTC)
    assert end == datetime(2025, 1, 16, 8, 0, tzinfo=UTC)
