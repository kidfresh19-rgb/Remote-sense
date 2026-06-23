"""No-DB tests for the live-collection wiring (D4-live): the field->AOI conversion, the pure
forward-fill due-selection, the core index set, and that the Celery tasks register. The DB-backed
orchestration lives in test_tasks_db.py (skips without a database)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from geoalchemy2.shape import from_shape
from rs_analysis import get_index
from rs_core.models import Field
from rs_imagery import SceneRef
from shapely.geometry import MultiPolygon, Polygon, shape

import services.worker.tasks.collection as collection
from services.worker.celery_app import celery
from services.worker.planning import select_forward_fill_due
from services.worker.tasks import CORE_INDICES, field_to_aoi
from services.worker.tasks.collection import _enqueue_collect_pass, _split_passes_by_lane


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
        "collection.collect_pass",
        "collection.forward_fill_field",
        "collection.scan_and_enqueue",
        "interpret.field_pass",
        "sync.publish_farm",
    ):
        assert name in celery.tasks


# -- CN-1: Collect now lane split. The user-initiated "Collect now" promotes the newest passes to
# the interactive lane so the chart's right edge populates promptly; the deep-history tail and every
# pass of the nightly sweep / ingest stay on the bulk lane. -------------------------------------


def _ref(scene_id: str, day: date) -> SceneRef:
    """A metadata-only scene ref on `day`; footprint is unused by the lane split and enqueue."""
    return SceneRef(
        scene_id=scene_id,
        provider="cdse",
        sensing_datetime=datetime(day.year, day.month, day.day, tzinfo=UTC),
        footprint={"type": "Polygon", "coordinates": []},
    )


def test_split_passes_non_interactive_keeps_all_on_bulk() -> None:
    # The nightly sweep / ingest never set interactive, so every pass stays on the bulk lane in the
    # original oldest-first discovery order (an in-flight 1-arg backfill_field message lands here).
    plan = [_ref("a", date(2026, 1, 1)), _ref("b", date(2026, 2, 1))]
    interactive_passes, bulk_passes = _split_passes_by_lane(plan, interactive=False, head=5)
    assert interactive_passes == []
    assert bulk_passes == plan


def test_split_passes_promotes_the_newest_head_to_interactive() -> None:
    # plan_backfill_scenes returns oldest-first, so the split must promote the NEWEST passes - a
    # naive plan[:head] would route exactly the wrong (oldest) ones.
    plan = [
        _ref("old", date(2026, 1, 1)),
        _ref("mid", date(2026, 3, 1)),
        _ref("new", date(2026, 6, 1)),
    ]
    interactive_passes, bulk_passes = _split_passes_by_lane(plan, interactive=True, head=2)
    assert [r.scene_id for r in interactive_passes] == ["new", "mid"]  # newest two, newest-first
    assert [r.scene_id for r in bulk_passes] == ["old"]


def test_split_passes_small_plan_goes_all_interactive() -> None:
    # A plan no longer than head goes entirely to the interactive lane (bounded either way).
    plan = [_ref("a", date(2026, 1, 1)), _ref("b", date(2026, 2, 1))]
    interactive_passes, bulk_passes = _split_passes_by_lane(plan, interactive=True, head=5)
    assert {r.scene_id for r in interactive_passes} == {"a", "b"}
    assert bulk_passes == []


def test_enqueue_collect_pass_routes_to_the_requested_lane(monkeypatch) -> None:
    calls: list[dict] = []

    class _FakeTask:
        def delay(self, *args: object) -> None:
            calls.append({"queue": None, "args": args})

        def apply_async(self, *, args: list[object], queue: str) -> None:
            calls.append({"queue": queue, "args": tuple(args)})

    monkeypatch.setattr(collection, "collect_pass_task", _FakeTask())
    ref = _ref("s1", date(2026, 6, 1))

    _enqueue_collect_pass("field-1", ref, queue="interactive")  # user-initiated head
    _enqueue_collect_pass("field-1", ref)  # bulk default (tail / sweep / ingest)

    assert [c["queue"] for c in calls] == ["interactive", None]
    # Both thread the serialised SceneRef as the 4th arg (2b fast path); scene_id + pass_date stay
    # positional so an in-flight 3-arg message still deserialises and takes the search fallback.
    for c in calls:
        field_id, scene_id, pass_date, scene_ref_json = c["args"]
        assert (field_id, scene_id, pass_date) == ("field-1", "s1", "2026-06-01")
        assert SceneRef.model_validate_json(scene_ref_json).scene_id == "s1"


def test_fan_out_backfill_interactive_flag_defaults_off() -> None:
    # The flag is an optional keyword, so an in-flight 1-arg backfill_field message (acks_late
    # redelivery) still binds and the fan-out runs entirely on the bulk lane.
    import inspect

    assert (
        inspect.signature(collection._fan_out_backfill).parameters["interactive"].default is False
    )
