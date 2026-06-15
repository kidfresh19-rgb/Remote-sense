"""Targeted "collect specific dates": the pure date-snapping kernel (`_snap_dates`) against
synthetic scenes - no DB, no network. The DB+adapter wrapper (`_plan_collect_dates`) and the
endpoint are exercised in the DB-gated suite (tests/test_workspace_db.py)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from services.worker.tasks.collection import _snap_dates


def _scene(day: str, scene_id: str) -> SimpleNamespace:
    # _snap_dates only reads .sensing_datetime and .scene_id, so a light stand-in suffices.
    return SimpleNamespace(
        sensing_datetime=datetime.fromisoformat(f"{day}T00:00:00+00:00").astimezone(UTC),
        scene_id=scene_id,
    )


def test_exact_day_resolves_with_zero_gap() -> None:
    resolved, skipped, to_enqueue = _snap_dates(
        [date(2025, 3, 5)], [_scene("2025-03-05", "A")], frozenset()
    )
    assert skipped == []
    assert resolved[0]["scene_id"] == "A"
    assert resolved[0]["day_gap"] == 0
    assert to_enqueue == {"A": "2025-03-05"}


def test_within_tolerance_snaps_and_reports_gap() -> None:
    resolved, skipped, to_enqueue = _snap_dates(
        [date(2025, 3, 5)], [_scene("2025-03-08", "A")], frozenset()
    )
    assert resolved[0]["scene_id"] == "A"
    assert resolved[0]["day_gap"] == 3  # +/-7d tolerance
    assert to_enqueue == {"A": "2025-03-08"}


def test_outside_tolerance_is_skipped() -> None:
    resolved, skipped, to_enqueue = _snap_dates(
        [date(2025, 3, 5)], [_scene("2025-03-20", "A")], frozenset()
    )
    assert resolved == []
    assert skipped == ["2025-03-05"]
    assert to_enqueue == {}


def test_closest_scene_wins() -> None:
    # 2025-03-06: A is -3 days, B is +1 day -> B is closer.
    resolved, _, to_enqueue = _snap_dates(
        [date(2025, 3, 6)],
        [_scene("2025-03-03", "A"), _scene("2025-03-07", "B")],
        frozenset(),
    )
    assert resolved[0]["scene_id"] == "B"
    assert to_enqueue == {"B": "2025-03-07"}


def test_tie_prefers_earlier_acquisition() -> None:
    # 2025-03-05: A is -3, B is +3 -> tie on |gap|, the earlier (on-or-before) acquisition wins.
    resolved, _, _ = _snap_dates(
        [date(2025, 3, 5)],
        [_scene("2025-03-02", "A"), _scene("2025-03-08", "B")],
        frozenset(),
    )
    assert resolved[0]["scene_id"] == "A"
    assert resolved[0]["day_gap"] == -3


def test_two_dates_snapping_to_one_scene_dedup_to_one_enqueue() -> None:
    resolved, _, to_enqueue = _snap_dates(
        [date(2025, 3, 4), date(2025, 3, 6)], [_scene("2025-03-05", "A")], frozenset()
    )
    assert len(resolved) == 2  # both dates resolve...
    assert to_enqueue == {"A": "2025-03-05"}  # ...to a single collection


def test_already_processed_scene_resolves_but_is_not_re_enqueued() -> None:
    resolved, skipped, to_enqueue = _snap_dates(
        [date(2025, 3, 5)], [_scene("2025-03-05", "A")], frozenset({"A"})
    )
    assert resolved[0]["scene_id"] == "A"  # still reported as resolved
    assert skipped == []
    assert to_enqueue == {}  # but not collected again (idempotent)


def test_dates_are_deduped_and_sorted() -> None:
    resolved, _, _ = _snap_dates(
        [date(2025, 3, 6), date(2025, 3, 4), date(2025, 3, 4)],
        [_scene("2025-03-05", "A")],
        frozenset(),
    )
    requested = [r["requested_date"] for r in resolved]
    assert requested == ["2025-03-04", "2025-03-06"]  # one 03-04 row, ascending
