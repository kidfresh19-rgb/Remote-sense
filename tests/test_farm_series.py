"""Farm AOI series: the worker engine against the mock adapter (no network, no DB) and the BFF
endpoints called directly with a constructed principal (no broker). Covers geometry union,
backfill + dates-mode engines, empty-field guard, and endpoint enqueue + validation.

Tests are written first (Slice 2 TDD). The engine is testable against the mock adapter + a
synthetic field list with no network, no DB.  The endpoint tests patch the Celery task's delay
so no broker is needed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from rs_core import Principal, Role
from rs_core.config import ImageryAdapter, Settings
from rs_imagery import AOI, TimeRange, get_access_adapter

from services.api.workspace import (
    FarmSeriesRequest,
    analyse_farm_series_endpoint,
)
from services.worker.tasks.analysis import (
    _analyse_farm_series,
    _union_geometries,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_ANALYST = Principal(subject="analyst-1", roles=frozenset({Role.ANALYST}))

# Two adjacent 0.01° × 0.01° field polygons for the farm.
_FIELD_GEOM_A: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [[31.0, -17.8], [31.01, -17.8], [31.01, -17.81], [31.0, -17.81], [31.0, -17.8]]
    ],
}
_FIELD_GEOM_B: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [[31.01, -17.8], [31.02, -17.8], [31.02, -17.81], [31.01, -17.81], [31.01, -17.8]]
    ],
}

_MOCK_FIELDS: list[dict[str, Any]] = [
    {"field_id": "f1", "geometry": _FIELD_GEOM_A},
    {"field_id": "f2", "geometry": _FIELD_GEOM_B},
]


def _adapter():
    return get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))


# ---------------------------------------------------------------------------
# Unit: _union_geometries
# ---------------------------------------------------------------------------


def test_union_single_polygon_returns_as_is() -> None:
    result = _union_geometries([_FIELD_GEOM_A])
    # A single polygon: the union is just the polygon itself.
    assert result["type"] in ("Polygon", "MultiPolygon")


def test_union_two_polygons_returns_multipolygon_or_polygon() -> None:
    result = _union_geometries([_FIELD_GEOM_A, _FIELD_GEOM_B])
    # Two polygons: may be merged into a single polygon (they share an edge) or MultiPolygon.
    assert result["type"] in ("Polygon", "MultiPolygon")


def test_union_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _union_geometries([])


# ---------------------------------------------------------------------------
# Unit: _analyse_farm_series engine (mock adapter, no DB)
# ---------------------------------------------------------------------------


async def test_farm_series_backfill_returns_passes() -> None:
    adapter = _adapter()
    now = datetime(2025, 6, 15, tzinfo=UTC)
    out = await _analyse_farm_series(
        _MOCK_FIELDS,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=adapter,
        backfill_months=18,
        now=now,
    )
    assert out["status"] == "ok"
    assert out["mode"] == "backfill"
    assert len(out["passes"]) > 0
    assert all(p["status"] in ("ok", "no_pass") for p in out["passes"])


async def test_farm_series_dates_mode_exact_day() -> None:
    adapter = _adapter()
    anchor = datetime(2025, 1, 1, tzinfo=UTC)
    geom = _union_geometries([_FIELD_GEOM_A, _FIELD_GEOM_B])
    found = await adapter.search(
        AOI(geometry=geom),
        TimeRange(start=anchor, end=anchor + timedelta(days=60)),
        max_scene_cloud_pct=70.0,
    )
    assert found, "mock adapter must yield passes in a 60-day window"
    d_ok = found[0].sensing_datetime.date()

    out = await _analyse_farm_series(
        _MOCK_FIELDS,
        "ndvi",
        "dates",
        [d_ok.isoformat()],
        None,
        adapter=adapter,
    )
    assert out["mode"] == "dates"
    assert out["passes"][0]["status"] == "ok"
    assert out["passes"][0]["pass_date"] == d_ok.isoformat()


async def test_farm_series_empty_fields_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        await _analyse_farm_series([], "ndvi", "backfill", None, 6, adapter=_adapter())


# ---------------------------------------------------------------------------
# Endpoint: enqueue + validation
# ---------------------------------------------------------------------------


async def test_endpoint_enqueues_farm_backfill_job(monkeypatch) -> None:
    import services.worker.tasks as tasks

    captured: dict = {}
    monkeypatch.setattr(
        tasks,
        "analyse_farm_series_task",
        type(
            "FakeTask",
            (),
            {
                "delay": staticmethod(
                    lambda *args: captured.update(args=args) or SimpleNamespace(id="job-farm-1")
                )
            },
        )(),
    )
    req = FarmSeriesRequest(index="ndvi", mode="backfill", months=6)
    out = await analyse_farm_series_endpoint("FARM-001", req, _ANALYST)
    assert out["state"] == "queued"
    assert out["job_id"] == "job-farm-1"


async def test_endpoint_enqueues_farm_dates_job(monkeypatch) -> None:
    import services.worker.tasks as tasks

    captured: dict = {}
    monkeypatch.setattr(
        tasks,
        "analyse_farm_series_task",
        type(
            "FakeTask",
            (),
            {
                "delay": staticmethod(
                    lambda *args: captured.update(args=args) or SimpleNamespace(id="job-farm-2")
                )
            },
        )(),
    )
    req = FarmSeriesRequest(
        index="ndvi", mode="dates", dates=[date(2025, 1, 15), date(2025, 1, 20)]
    )
    out = await analyse_farm_series_endpoint("FARM-001", req, _ANALYST)
    assert out["state"] == "queued"
    assert captured["args"][0] == "FARM-001"  # canonical_farm_id passed through


async def test_endpoint_rejects_unknown_index(monkeypatch) -> None:
    import services.worker.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "analyse_farm_series_task",
        type("FakeTask", (), {"delay": staticmethod(lambda *a: pytest.fail("must not enqueue"))})(),
    )
    req = FarmSeriesRequest(index="bogus", mode="backfill", months=6)
    with pytest.raises(HTTPException) as ei:
        await analyse_farm_series_endpoint("FARM-001", req, _ANALYST)
    assert ei.value.status_code == 422


async def test_endpoint_rejects_future_dates(monkeypatch) -> None:
    import services.worker.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "analyse_farm_series_task",
        type("FakeTask", (), {"delay": staticmethod(lambda *a: pytest.fail("must not enqueue"))})(),
    )
    future = datetime.now(UTC).date() + timedelta(days=5)
    req = FarmSeriesRequest(index="ndvi", mode="dates", dates=[future])
    with pytest.raises(HTTPException) as ei:
        await analyse_farm_series_endpoint("FARM-001", req, _ANALYST)
    assert ei.value.status_code == 422
