"""No-DB tests for the live-collection wiring (D4-live): the field->AOI conversion, the pure
forward-fill due-selection, the core index set, and that the Celery tasks register. The DB-backed
orchestration lives in test_tasks_db.py (skips without a database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoalchemy2.shape import from_shape
from rs_analysis import get_index
from rs_core.models import Field
from shapely.geometry import MultiPolygon, Polygon, shape

from services.worker.celery_app import celery
from services.worker.planning import select_forward_fill_due
from services.worker.tasks import CORE_INDICES, field_to_aoi


def _mp(lon: float, lat: float, side: float) -> MultiPolygon:
    h = side / 2
    return MultiPolygon(
        [
            Polygon(
                [
                    (lon - h, lat - h),
                    (lon + h, lat - h),
                    (lon + h, lat + h),
                    (lon - h, lat + h),
                    (lon - h, lat - h),
                ]
            )
        ]
    )


def test_field_to_aoi_round_trips_boundary() -> None:
    field = Field(boundary=from_shape(_mp(31.05, -17.83, 0.01), srid=4326))
    aoi = field_to_aoi(field)
    assert aoi.crs == "EPSG:4326"
    assert aoi.geometry["type"] in {"Polygon", "MultiPolygon"}
    assert shape(aoi.geometry).covers(shape({"type": "Point", "coordinates": [31.05, -17.83]}))


def test_core_indices_resolve_to_real_specs() -> None:
    assert CORE_INDICES == ["ndvi", "evi2", "savi", "ndre", "ndmi"]
    for name in CORE_INDICES:
        assert get_index(name).formula_version  # resolves to a real, versioned index spec


def test_select_forward_fill_due_filters_correctly() -> None:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    recent = now - timedelta(days=1)
    stale = now - timedelta(days=6)
    candidates = [
        ("f-due", stale, True),  # backfilled + a cadence elapsed -> due
        ("f-recent", recent, True),  # polled yesterday -> not due
        ("f-never", None, True),  # backfilled, never forward-polled -> due
        ("f-backfilling", stale, False),  # backfill not complete -> excluded
    ]
    assert select_forward_fill_due(candidates, now) == ["f-due", "f-never"]


def test_collection_tasks_are_registered() -> None:
    for name in (
        "collection.backfill_field",
        "collection.forward_fill_field",
        "collection.scan_and_enqueue",
        "interpret.field_pass",
        "sync.publish_farm",
    ):
        assert name in celery.tasks
