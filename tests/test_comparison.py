"""Pure, zero-DB / zero-network unit tests for the comparison engine (backlog 0003): group
reference-pass selection (quorum, nearest-within-N, sit-out, clear fraction) and standing
(crop-stratified percentile + the sparse-crop status-distribution fallback) on synthetic series.
Prior art: tests/test_geo.py, tests/test_indices.py."""

from __future__ import annotations

from datetime import date

from rs_core.comparison import (
    MemberPass,
    compute_standing,
    crop_standing,
    select_group_reference_pass,
)


def _p(y: int, m: int, d: int, value: float) -> MemberPass:
    return MemberPass(pass_date=date(y, m, d), value=value)


# --- reference-pass selection ---


def test_reference_pass_most_recent_with_quorum() -> None:
    """The most recent date where a quorum (default 50%) holds a clear read wins; members within
    +/- N days align to it, and a member outside the window sits out."""
    members = {
        "A": [_p(2026, 5, 17, 0.8), _p(2026, 5, 5, 0.7)],
        "B": [_p(2026, 5, 15, 0.6)],
        "C": [_p(2026, 4, 1, 0.5)],  # 46 days from 5/17 -> outside +/-14, sits out
    }
    ref = select_group_reference_pass(members)
    assert ref is not None
    assert ref.reference_date == date(2026, 5, 17)
    assert ref.total == 3
    assert ref.contributing == 2
    by_id = {c.member_id: c for c in ref.contributions}
    assert set(by_id) == {"A", "B"}
    assert by_id["A"].value == 0.8 and by_id["A"].day_offset == 0
    assert by_id["B"].value == 0.6 and by_id["B"].day_offset == -2


def test_reference_pass_contributes_nearest_clear_pass() -> None:
    """When a member has several passes inside the window it contributes the nearest one, not the
    oldest or an arbitrary one."""
    members = {
        "A": [_p(2026, 5, 1, 0.4), _p(2026, 5, 10, 0.9)],
        "B": [_p(2026, 5, 8, 0.6)],
    }
    ref = select_group_reference_pass(members)
    assert ref is not None
    assert ref.reference_date == date(2026, 5, 10)
    a = next(c for c in ref.contributions if c.member_id == "A")
    assert a.pass_date == date(2026, 5, 10)  # nearest to 5/10, not 5/1
    assert a.value == 0.9


def test_reference_pass_falls_back_when_recent_date_lacks_quorum() -> None:
    """A recent pass held by too few members is skipped for an older date that does reach quorum."""
    members = {
        "A": [_p(2026, 5, 20, 0.8)],  # recent but alone -> 1/3 < 0.5
        "B": [_p(2026, 5, 2, 0.6)],
        "C": [_p(2026, 5, 3, 0.5)],
    }
    ref = select_group_reference_pass(members, window_days=5)
    assert ref is not None
    assert ref.reference_date == date(2026, 5, 3)
    assert {c.member_id for c in ref.contributions} == {"B", "C"}
    assert ref.clear_fraction == 2 / 3


def test_reference_pass_quorum_is_configurable() -> None:
    """A stricter quorum rejects a date a looser one would accept."""
    members = {
        "A": [_p(2026, 5, 17, 0.8)],
        "B": [_p(2026, 5, 16, 0.6)],
        "C": [_p(2026, 1, 1, 0.5)],  # far away, always sits out
    }
    assert select_group_reference_pass(members, quorum=0.5) is not None
    assert select_group_reference_pass(members, quorum=0.9) is None


def test_reference_pass_window_is_configurable() -> None:
    """Narrowing the window drops a member that a wider one would have aligned."""
    members = {
        "A": [_p(2026, 5, 17, 0.8)],
        "B": [_p(2026, 5, 5, 0.6)],  # 12 days from 5/17
    }
    wide = select_group_reference_pass(members, window_days=14, quorum=1.0)
    assert wide is not None and wide.contributing == 2
    assert select_group_reference_pass(members, window_days=5, quorum=1.0) is None


def test_reference_pass_none_for_empty_or_no_passes() -> None:
    assert select_group_reference_pass({}) is None
    assert select_group_reference_pass({"A": [], "B": []}) is None


# --- standing ---


def test_crop_standing_percentile_when_cohort_is_dense() -> None:
    """A crop with enough same-crop peers ranks the farm by raw-NDVI percentile (at-or-below)."""
    standing = crop_standing(
        crop="maize",
        target_value=0.6,
        cohort_values=[0.2, 0.4, 0.6, 0.8],
        target_status="moderate",
        group_statuses=["healthy", "moderate", "moderate", "stressed"],
    )
    assert standing.method == "percentile"
    assert standing.cohort_size == 4
    assert standing.value == 0.6
    assert standing.percentile == 75.0  # 3 of 4 at or below 0.6


def test_crop_standing_falls_back_to_status_distribution_when_sparse() -> None:
    """Too few same-crop peers -> the crop-aware status distribution across the whole group."""
    standing = crop_standing(
        crop="tobacco",
        target_value=0.7,
        cohort_values=[0.7, 0.5],
        target_status="healthy",
        group_statuses=["healthy", "moderate", "stressed", "healthy"],
        min_cohort=4,
    )
    assert standing.method == "status_distribution"
    assert standing.cohort_size == 2
    assert standing.status == "healthy"
    assert standing.status_distribution == {"healthy": 0.5, "moderate": 0.25, "stressed": 0.25}
    assert standing.percentile is None


def test_compute_standing_stratifies_per_crop_never_across_crops() -> None:
    """A mixed maize+tobacco farm gets one standing per crop; maize ranks only against maize, so a
    high tobacco value never inflates the maize percentile."""
    standings = compute_standing(
        target_crop_values={"maize": 0.6, "tobacco": 0.7},
        cohort_crop_values={"maize": [0.2, 0.4, 0.6, 0.8], "tobacco": [0.7, 0.5]},
        target_crop_statuses={"maize": "moderate", "tobacco": "healthy"},
        group_statuses=["healthy", "moderate", "stressed", "healthy"],
    )
    assert [s.crop for s in standings] == ["maize", "tobacco"]  # name-ordered
    maize, tobacco = standings
    assert maize.method == "percentile" and maize.percentile == 75.0
    assert tobacco.method == "status_distribution"  # only 2 tobacco peers
