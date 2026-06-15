"""AOI Studio multi-pass preview: the worker engine against the mock adapter (no network, no DB)
and the BFF endpoints called directly with a constructed principal (no broker). Covers exact-day
batch matching, the backfill sweep, the AOI-size guard, request validation, and the job-status
mapping."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rs_core import Principal, Role
from rs_core.config import ImageryAdapter, Settings
from rs_imagery import AOI, TimeRange, get_access_adapter

from services.api.workspace import (
    AOISeriesRequest,
    analyse_aoi_series_endpoint,
)
from services.api.workspace.analyse import MAX_BATCH_DATES, _job_status
from services.worker.planning import backfill_window
from services.worker.tasks.analysis import MAX_AOI_SPAN_DEG, _analyse_aoi_series

_GEOM: dict = {
    "type": "Polygon",
    "coordinates": [
        [[31.0, -17.8], [31.01, -17.8], [31.01, -17.81], [31.0, -17.81], [31.0, -17.8]]
    ],
}
_AOI = AOI(geometry=_GEOM)
_ANALYST = Principal(subject="analyst-1", roles=frozenset({Role.ANALYST}))


def _adapter():
    return get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))


def _sod(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


# --------------------------------------------------------------------------- engine: dates mode


async def test_dates_mode_matches_exact_day_and_marks_gaps_no_pass() -> None:
    # The mock anchors its 5-day grid at the search-range start, and dates-mode re-anchors the
    # search at start_of_day(min(dates)) - so the same scene id (hence the same cloud) recurs and
    # the min date resolves to a real pass, while the day after it (off the 5-day grid) cannot.
    adapter = _adapter()
    anchor = datetime(2025, 1, 1, tzinfo=UTC)
    found = await adapter.search(
        _AOI, TimeRange(start=anchor, end=anchor + timedelta(days=60)), max_scene_cloud_pct=70.0
    )
    assert found, "mock should yield at least one usable scene in a 60-day window"
    d_ok = found[0].sensing_datetime.date()
    gap = d_ok + timedelta(days=1)

    out = await _analyse_aoi_series(
        _GEOM, "ndvi", "dates", [d_ok.isoformat(), gap.isoformat()], None, adapter=adapter
    )

    assert out["mode"] == "dates"
    passes = out["passes"]
    assert [p["requested_date"] for p in passes] == [d_ok.isoformat(), gap.isoformat()]
    assert passes[0]["status"] == "ok"
    assert passes[0]["pass_date"] == d_ok.isoformat()  # exact day, not snapped
    assert 0.0 <= passes[0]["clear_fraction"] <= 1.0
    assert passes[1]["status"] == "no_pass"
    assert out["resolved"] == 1
    assert out["requested"] == 2


async def test_dates_mode_dedups_and_sorts_requested_dates() -> None:
    adapter = _adapter()
    d = date(2025, 3, 10)
    # Unsorted, with a duplicate: the engine should collapse to two ascending requested dates.
    raw = [(d + timedelta(days=2)).isoformat(), d.isoformat(), d.isoformat()]
    out = await _analyse_aoi_series(_GEOM, "ndvi", "dates", raw, None, adapter=adapter)
    requested = [p["requested_date"] for p in out["passes"]]
    assert requested == sorted(set(requested))
    assert len(requested) == 2


async def test_dates_mode_requires_at_least_one_date() -> None:
    with pytest.raises(ValueError, match="at least one date"):
        await _analyse_aoi_series(_GEOM, "ndvi", "dates", [], None, adapter=_adapter())


# --------------------------------------------------------------------------- engine: backfill mode


async def test_backfill_mode_returns_window_passes_ascending() -> None:
    adapter = _adapter()
    now = datetime(2025, 6, 15, tzinfo=UTC)
    depth = 6
    window_start, _ = backfill_window(now.date(), depth)
    expected = sorted(
        {
            s.sensing_datetime.date()
            for s in await adapter.search(
                _AOI, TimeRange(start=_sod(window_start), end=now), max_scene_cloud_pct=70.0
            )
        }
    )

    out = await _analyse_aoi_series(
        _GEOM, "ndvi", "backfill", None, depth, adapter=adapter, backfill_months=18, now=now
    )

    assert out["mode"] == "backfill"
    got = [p["pass_date"] for p in out["passes"]]
    assert got == [d.isoformat() for d in expected]  # ascending, deterministic against the adapter
    assert got == sorted(got)
    assert all(p["status"] == "ok" for p in out["passes"])
    assert out["resolved"] == len(out["passes"])


async def test_backfill_mode_clamps_months_to_configured_depth() -> None:
    # Asking for more months than configured must not search beyond the configured horizon.
    adapter = _adapter()
    now = datetime(2025, 6, 15, tzinfo=UTC)
    out = await _analyse_aoi_series(
        _GEOM, "ndvi", "backfill", None, 999, adapter=adapter, backfill_months=12, now=now
    )
    horizon_start, _ = backfill_window(now.date(), 12)
    assert all(p["pass_date"] >= horizon_start.isoformat() for p in out["passes"])


# --------------------------------------------------------------------------- engine: guards


async def test_oversized_aoi_is_rejected() -> None:
    span = MAX_AOI_SPAN_DEG + 1.0
    big = {
        "type": "Polygon",
        "coordinates": [
            [
                [20.0, -20.0],
                [20.0 + span, -20.0],
                [20.0 + span, -20.0 + span],
                [20.0, -20.0],
                [20.0, -20.0],
            ]
        ],
    }
    with pytest.raises(ValueError, match="too large"):
        await _analyse_aoi_series(
            big,
            "ndvi",
            "backfill",
            None,
            6,
            adapter=_adapter(),
            backfill_months=18,
            now=datetime(2025, 6, 15, tzinfo=UTC),
        )


# --------------------------------------------------------------------------- endpoint: enqueue


async def test_endpoint_enqueues_dates_job(monkeypatch) -> None:
    import services.worker.tasks as tasks

    captured: dict = {}
    monkeypatch.setattr(
        tasks.analyse_aoi_series_task,
        "delay",
        lambda *args: captured.update(args=args) or SimpleNamespace(id="job-abc"),
    )
    req = AOISeriesRequest(
        geometry=_GEOM, index="ndvi", mode="dates", dates=[date(2025, 1, 15), date(2025, 1, 20)]
    )
    out = await analyse_aoi_series_endpoint(req, _ANALYST)
    assert out == {"job_id": "job-abc", "state": "queued"}
    # Dates are passed as ISO strings (JSON-safe for the broker), months is None in dates mode.
    assert captured["args"] == (_GEOM, "ndvi", "dates", ["2025-01-15", "2025-01-20"], None)


async def test_endpoint_enqueues_backfill_job(monkeypatch) -> None:
    import services.worker.tasks as tasks

    captured: dict = {}
    monkeypatch.setattr(
        tasks.analyse_aoi_series_task,
        "delay",
        lambda *args: captured.update(args=args) or SimpleNamespace(id="job-bf"),
    )
    req = AOISeriesRequest(geometry=_GEOM, index="ndvi", mode="backfill", months=6)
    out = await analyse_aoi_series_endpoint(req, _ANALYST)
    assert out["state"] == "queued"
    assert captured["args"] == (_GEOM, "ndvi", "backfill", None, 6)


# --------------------------------------------------------------------------- endpoint: validation


async def test_endpoint_rejects_unknown_index(monkeypatch) -> None:
    import services.worker.tasks as tasks

    monkeypatch.setattr(
        tasks.analyse_aoi_series_task, "delay", lambda *a: pytest.fail("must not enqueue")
    )
    req = AOISeriesRequest(geometry=_GEOM, index="bogus", mode="dates", dates=[date(2025, 1, 15)])
    with pytest.raises(HTTPException) as ei:
        await analyse_aoi_series_endpoint(req, _ANALYST)
    assert ei.value.status_code == 422


async def test_endpoint_rejects_too_many_dates(monkeypatch) -> None:
    import services.worker.tasks as tasks

    monkeypatch.setattr(
        tasks.analyse_aoi_series_task, "delay", lambda *a: pytest.fail("must not enqueue")
    )
    too_many = [date(2025, 1, 1) + timedelta(days=i) for i in range(MAX_BATCH_DATES + 1)]
    req = AOISeriesRequest(geometry=_GEOM, index="ndvi", mode="dates", dates=too_many)
    with pytest.raises(HTTPException) as ei:
        await analyse_aoi_series_endpoint(req, _ANALYST)
    assert ei.value.status_code == 422


async def test_endpoint_rejects_future_dates(monkeypatch) -> None:
    import services.worker.tasks as tasks

    monkeypatch.setattr(
        tasks.analyse_aoi_series_task, "delay", lambda *a: pytest.fail("must not enqueue")
    )
    future = datetime.now(UTC).date() + timedelta(days=5)
    req = AOISeriesRequest(geometry=_GEOM, index="ndvi", mode="dates", dates=[future])
    with pytest.raises(HTTPException) as ei:
        await analyse_aoi_series_endpoint(req, _ANALYST)
    assert ei.value.status_code == 422


async def test_endpoint_rejects_out_of_range_months(monkeypatch) -> None:
    import services.worker.tasks as tasks

    monkeypatch.setattr(
        tasks.analyse_aoi_series_task, "delay", lambda *a: pytest.fail("must not enqueue")
    )
    req = AOISeriesRequest(geometry=_GEOM, index="ndvi", mode="backfill", months=999)
    with pytest.raises(HTTPException) as ei:
        await analyse_aoi_series_endpoint(req, _ANALYST)
    assert ei.value.status_code == 422


# --------------------------------------------------------------------------- job status mapping


def _patch_async_result(monkeypatch, *, state, result=None, info=None) -> None:
    class _FakeAsyncResult:
        def __init__(self, job_id: str, app=None) -> None:
            self.id = job_id

        @property
        def state(self):
            return state

        @property
        def result(self):
            return result

        @property
        def info(self):
            return info

    monkeypatch.setattr("celery.result.AsyncResult", _FakeAsyncResult)


def test_job_status_done(monkeypatch) -> None:
    payload = {"status": "ok", "mode": "dates", "passes": []}
    _patch_async_result(monkeypatch, state="SUCCESS", result=payload)
    assert _job_status("j") == {"job_id": "j", "state": "done", "result": payload}


def test_job_status_running_carries_progress(monkeypatch) -> None:
    _patch_async_result(monkeypatch, state="PROGRESS", info={"done": 2, "total": 5})
    out = _job_status("j")
    assert out["state"] == "running"
    assert out["progress"] == {"done": 2, "total": 5}


def test_job_status_error(monkeypatch) -> None:
    _patch_async_result(monkeypatch, state="FAILURE", result=ValueError("boom"))
    out = _job_status("j")
    assert out["state"] == "error"
    assert "boom" in out["error"]


def test_job_status_queued_for_pending(monkeypatch) -> None:
    _patch_async_result(monkeypatch, state="PENDING")
    assert _job_status("j") == {"job_id": "j", "state": "queued"}
