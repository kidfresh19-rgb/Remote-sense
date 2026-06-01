"""Collection-orchestration tests (Phase 3) against the mock adapter: search -> per-pass index
computation, dedup/resume via already_processed, and resolution honesty across the index suite.
No broker, no DB."""

from __future__ import annotations

from datetime import UTC, datetime

from rs_core.config import ImageryAdapter, Settings
from rs_imagery import AOI, TimeRange, get_access_adapter

from services.worker.collection import collect_field, collect_field_locked

_AOI = AOI(
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[31.0, -17.8], [31.01, -17.8], [31.01, -17.81], [31.0, -17.81], [31.0, -17.8]]
        ],
    }
)
_RANGE = TimeRange(
    start=datetime(2024, 10, 1, tzinfo=UTC),
    end=datetime(2024, 12, 1, tzinfo=UTC),
)
_INDICES = ["ndvi", "evi2", "savi", "ndre", "ndmi"]


def _adapter():
    return get_access_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))


async def test_collect_field_returns_all_indices_per_scene() -> None:
    results = await collect_field(adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=_INDICES)
    assert results, "mock search should yield scenes in this range"
    for r in results:
        names = {o.index_name for o in r.outputs}
        assert names == set(_INDICES)
        assert all(0.0 <= o.clear_fraction <= 1.0 for o in r.outputs)


async def test_collect_field_honours_native_resolution() -> None:
    results = await collect_field(adapter=_adapter(), aoi=_AOI, time_range=_RANGE, indices=_INDICES)
    out_by_name = {o.index_name: o for o in results[0].outputs}
    assert out_by_name["ndvi"].resolution_m == 10
    assert out_by_name["ndre"].resolution_m == 20  # B05 -> 20 m, not upsampled to 10 m
    assert out_by_name["ndmi"].resolution_m == 20


async def test_collect_field_skips_already_processed() -> None:
    adapter = _adapter()
    first = await collect_field(adapter=adapter, aoi=_AOI, time_range=_RANGE, indices=["ndvi"])
    done = frozenset(r.scene_id for r in first)

    # Re-running with every discovered scene marked done yields nothing (idempotent/resumable).
    second = await collect_field(
        adapter=adapter,
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
        already_processed=done,
    )
    assert second == []


async def test_collect_field_partial_resume() -> None:
    adapter = _adapter()
    first = await collect_field(adapter=adapter, aoi=_AOI, time_range=_RANGE, indices=["ndvi"])
    skip_one = frozenset(list(r.scene_id for r in first)[:1])
    remaining = await collect_field(
        adapter=adapter,
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
        already_processed=skip_one,
    )
    assert len(remaining) == len(first) - 1
    assert skip_one.isdisjoint({r.scene_id for r in remaining})


class _FakeRedis:
    """In-memory stand-in for the enqueue lock's redis client (SET NX + compare-and-delete)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, token = keys_and_args[0], keys_and_args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


_KEY = "collect:field:f1:v1"


async def test_collect_field_locked_runs_and_releases_when_free() -> None:
    redis = _FakeRedis()
    results = await collect_field_locked(
        lock_client=redis,
        collection_key=_KEY,
        adapter=_adapter(),
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
    )
    assert results, "with the lock free, collection should run and yield scenes"
    assert redis.store == {}  # lock released after the run


async def test_collect_field_locked_skips_when_already_held() -> None:
    redis = _FakeRedis()
    redis.store[_KEY] = "held-by-another-worker"
    results = await collect_field_locked(
        lock_client=redis,
        collection_key=_KEY,
        adapter=_adapter(),
        aoi=_AOI,
        time_range=_RANGE,
        indices=["ndvi"],
    )
    assert results is None  # R-1: another worker owns the unit, so skip rather than double-process
    assert redis.store[_KEY] == "held-by-another-worker"  # untouched
