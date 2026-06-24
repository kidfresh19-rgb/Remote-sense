"""AOI Studio multi-pass preview: the worker engine against the mock adapter (no network, no DB)
and the BFF endpoints called directly with a constructed principal (no broker). Covers exact-day
batch matching, the backfill sweep, the AOI-size guard, request validation, and the job-status
mapping."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog
from fastapi import HTTPException
from rs_core import Principal, Role
from rs_core.cache import RedisJsonCache
from rs_core.config import ImageryAdapter, Settings
from rs_imagery import AOI, TimeRange, get_access_adapter
from rs_imagery.adapters.mock import MockAdapter

from services.api.workspace import (
    AOISeriesRequest,
    analyse_aoi_series_endpoint,
)
from services.api.workspace.analyse import MAX_BATCH_DATES, _job_status
from services.worker.planning import backfill_window
from services.worker.tasks.analysis import (
    _INTERP_PAD_DAYS,
    MAX_AOI_SPAN_DEG,
    ResultCache,
    _analyse_aoi_series,
    _analyse_aoi_series_multi,
)

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


async def test_dates_mode_exact_and_interpolated_and_no_pass() -> None:
    """dates mode fully exercised:
    - a date that falls on the mock's padded grid → 'ok' with that exact pass_date
    - a date between two grid points → 'interpolated' carrying both source dates
    - a date with no 'after' bracket (sparse adapter) → 'no_pass'

    The engine now pads the archive search by ±_INTERP_PAD_DAYS so bracketing scenes exist
    for edge dates. With revisit=5 and pad=7, the padded search starts at min(dates)-7, giving
    grid points at min-7, min-2, min+3, min+8 ... So min+3 is an exact grid hit while min
    itself sits between min-2 and min+3 (→ interpolated).
    """
    from rs_imagery.adapters.mock import MockAdapter

    adapter = _adapter()
    d_min = date(2025, 1, 1)
    # min-7 + 2*revisit = min-7+10 = min+3 → exact grid point for a request anchored at min
    d_on_grid = d_min + timedelta(days=3)
    d_off_grid = d_min  # between the min-2 and min+3 grid points → interpolated

    # Guard: verify the expected grid scene survives the cloud filter before asserting.
    padded_start = _sod(d_min) - timedelta(days=_INTERP_PAD_DAYS)
    padded_end = _sod(d_on_grid) + timedelta(days=1 + _INTERP_PAD_DAYS)
    found = await adapter.search(
        _AOI, TimeRange(start=padded_start, end=padded_end), max_scene_cloud_pct=70.0
    )
    grid_dates = {s.sensing_datetime.date() for s in found}
    assert d_on_grid in grid_dates, (
        "expected grid point excluded by cloud filter; choose a different anchor"
    )

    out = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "dates",
        [d_off_grid.isoformat(), d_on_grid.isoformat()],
        None,
        adapter=adapter,
    )

    assert out["mode"] == "dates"
    passes = out["passes"]
    assert [p["requested_date"] for p in passes] == [d_off_grid.isoformat(), d_on_grid.isoformat()]

    # Off-grid date → interpolated from the two nearest real passes
    p_interp = passes[0]
    assert p_interp["status"] == "interpolated"
    assert p_interp["mean"] is not None
    assert 0.0 <= p_interp["clear_fraction"] <= 1.0
    assert p_interp["before_pass_date"] is not None
    assert p_interp["after_pass_date"] is not None
    assert p_interp["before_pass_date"] < d_off_grid.isoformat()
    assert p_interp["after_pass_date"] > d_off_grid.isoformat()

    # On-grid date → exact same-day match
    p_exact = passes[1]
    assert p_exact["status"] == "ok"
    assert p_exact["pass_date"] == d_on_grid.isoformat()
    assert 0.0 <= p_exact["clear_fraction"] <= 1.0

    # Both interpolated and ok count toward resolved
    assert out["resolved"] == 2
    assert out["requested"] == 2

    # no_pass: adapter returns only 1 scene (before the requested date, so no 'after' bracket)
    sparse = MockAdapter(revisit_days=5, scenes_per_search=1)
    out_none = await _analyse_aoi_series(
        _GEOM, "ndvi", "dates", [d_off_grid.isoformat()], None, adapter=sparse
    )
    assert out_none["passes"][0]["status"] == "no_pass"
    assert out_none["resolved"] == 0


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


# ------------------------------------------------------------------- engine: per-pass isolation


class _FlakyAdapter(MockAdapter):
    """A mock adapter whose fetch raises for one target scene, so a single bad granule can be made
    to fail mid-series (AN-2). The same instance discovers the scenes and runs the engine, so the
    target scene_id is the one the engine will actually fetch."""

    def __init__(self, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.fail_scene_id: str | None = None

    async def fetch(self, scene: object, *args: object, **kwargs: object):  # type: ignore[override]
        sid = getattr(scene, "scene_id", None)
        if self.fail_scene_id is not None and sid == self.fail_scene_id:
            raise RuntimeError("simulated cold-archived granule")
        return await super().fetch(scene, *args, **kwargs)  # type: ignore[arg-type]


async def test_backfill_isolates_a_failed_pass() -> None:
    # One scene's read fails; the series must still return every other pass, mark the bad one as an
    # `error` (not silently dropped), and not count it as resolved - the whole job no longer dies.
    now = datetime(2025, 6, 15, tzinfo=UTC)
    depth = 6
    window_start, _ = backfill_window(now.date(), depth)
    adapter = _FlakyAdapter()
    scenes = await adapter.search(
        _AOI, TimeRange(start=_sod(window_start), end=now), max_scene_cloud_pct=70.0
    )
    assert len(scenes) >= 3
    target = sorted(scenes, key=lambda s: s.sensing_datetime)[1]  # a middle pass fails
    adapter.fail_scene_id = target.scene_id

    ticks: list[tuple[int, int]] = []
    out = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        depth,
        adapter=adapter,
        backfill_months=18,
        now=now,
        on_progress=lambda d, t: ticks.append((d, t)),
    )

    passes = out["passes"]
    errors = [p for p in passes if p["status"] == "error"]
    assert len(errors) == 1  # exactly the one bad scene, isolated
    assert errors[0]["scene_id"] == target.scene_id
    assert errors[0]["index"] == "ndvi"
    assert errors[0]["detail"]  # carries the failure message for the results console
    assert out["resolved"] == len(passes) - 1  # the error pass is not counted resolved
    assert all(p["status"] == "ok" for p in passes if p["status"] != "error")
    n = len(passes)
    assert ticks[-1] == (n, n)  # progress still climbed to the full total


async def test_multi_backfill_isolates_a_failed_pass_per_index() -> None:
    # The all-indices engine shares one gather, so an isolated failure must surface inside the right
    # index series and leave every other index/pass intact.
    now = datetime(2025, 6, 15, tzinfo=UTC)
    depth = 6
    window_start, _ = backfill_window(now.date(), depth)
    adapter = _FlakyAdapter()
    scenes = await adapter.search(
        _AOI, TimeRange(start=_sod(window_start), end=now), max_scene_cloud_pct=70.0
    )
    target = sorted(scenes, key=lambda s: s.sensing_datetime)[1]
    adapter.fail_scene_id = target.scene_id

    out = await _analyse_aoi_series_multi(
        _GEOM,
        ["ndvi", "ndre"],
        "backfill",
        None,
        depth,
        adapter=adapter,
        backfill_months=18,
        now=now,
    )

    for name in ("ndvi", "ndre"):
        series = out["indices"][name]
        errors = [p for p in series["passes"] if p["status"] == "error"]
        assert len(errors) == 1  # the bad scene fails for each index that reads it
        assert errors[0]["scene_id"] == target.scene_id
        assert series["resolved"] == len(series["passes"]) - 1


# ----------------------------------------------------------- engine: adapter cache wiring (AN-1)


async def test_aoi_build_adapter_attaches_scene_caches_for_windowed_cog() -> None:
    """AN-1: the AOI Studio adapter shares the immutable cross-lane scene caches (cdse:scene_meta +
    cdse:scene_item) with the stored-collection path, plus its own AOI search cache, so a scene's
    metadata is read once across jobs and lanes. Lazy Redis client -> no network here."""
    from rs_imagery.adapters.windowed_cog import WindowedCogAdapter

    from services.worker.tasks.analysis import _build_adapter, _build_search_cache

    settings = Settings(imagery_adapter=ImageryAdapter.WINDOWED_COG)
    search_cache = _build_search_cache(settings)
    adapter, scene_caches = _build_adapter(settings, search_cache=search_cache)
    try:
        assert isinstance(adapter, WindowedCogAdapter)
        assert adapter._scene_meta_cache is not None  # the Phase 2a cross-lane cache
        assert adapter._scene_item_cache is not None  # the Phase 2b cross-lane cache
        assert adapter._search_cache is search_cache  # the AOI search cache is still wired
        assert len(scene_caches) == 2  # only the scene caches are returned to close
    finally:
        for c in scene_caches:
            await c.aclose()
        if search_cache is not None:
            await search_cache.aclose()


def test_aoi_build_adapter_passes_through_non_windowed_cog() -> None:
    """A non-windowed_cog adapter (the config switch, invariant 1) gets no caches to manage."""
    from rs_imagery.adapters.mock import MockAdapter

    from services.worker.tasks.analysis import _build_adapter

    adapter, scene_caches = _build_adapter(Settings(imagery_adapter=ImageryAdapter.MOCK))
    assert isinstance(adapter, MockAdapter)
    assert scene_caches == []


# --------------------------------------------------------------------------- engine: concurrency


class _ConcurrencyProbe(MockAdapter):
    """A mock adapter whose fetch sleeps and tracks how many fetches overlap, so the tests can
    assert passes run concurrently and never exceed the semaphore. Single event loop, so the
    active counter is mutated only at await boundaries - no lock needed."""

    def __init__(self, *, delay: float = 0.02, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def fetch(self, *args: object, **kwargs: object):  # type: ignore[override]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return await super().fetch(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            self.active -= 1


async def test_series_runs_passes_concurrently_bounded_by_the_semaphore() -> None:
    adapter = _ConcurrencyProbe(delay=0.02, scenes_per_search=12)
    out = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=adapter,
        backfill_months=18,
        now=datetime(2025, 6, 15, tzinfo=UTC),
        concurrency=4,
    )
    assert len(out["passes"]) >= 5  # enough passes to observe bounding
    assert adapter.max_active > 1  # genuinely concurrent, not serial
    assert adapter.max_active <= 4  # never exceeds the semaphore


async def test_series_concurrency_beats_serial_walltime() -> None:
    delay = 0.05
    adapter = _ConcurrencyProbe(delay=delay, scenes_per_search=10)
    start = time.monotonic()
    out = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=adapter,
        backfill_months=18,
        now=datetime(2025, 6, 15, tzinfo=UTC),
        concurrency=8,
    )
    elapsed = time.monotonic() - start
    n = len(out["passes"])
    assert n >= 5
    assert elapsed < n * delay  # strictly faster than running the passes serially


async def test_series_progress_is_monotonic_and_order_preserved() -> None:
    adapter = _ConcurrencyProbe(delay=0.01, scenes_per_search=8)
    ticks: list[tuple[int, int]] = []
    out = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=adapter,
        backfill_months=18,
        now=datetime(2025, 6, 15, tzinfo=UTC),
        concurrency=4,
        on_progress=lambda d, t: ticks.append((d, t)),
    )
    n = len(out["passes"])
    assert [d for d, _ in ticks] == list(range(1, n + 1))  # done climbs 1..n, once per pass
    assert all(t == n for _, t in ticks)  # total is constant
    got = [p["pass_date"] for p in out["passes"]]
    assert got == sorted(got)  # oldest-first order kept despite out-of-order settling


# --------------------------------------------------------------------------- engine: result cache

_NOW = datetime(2025, 6, 15, tzinfo=UTC)
_GEOM2: dict = {  # a different AOI -> different canonical geometry hash
    "type": "Polygon",
    "coordinates": [
        [[31.5, -17.8], [31.51, -17.8], [31.51, -17.81], [31.5, -17.81], [31.5, -17.8]]
    ],
}


class _SharedFakeRedis:
    """In-memory async stand-in sharing one store across cache instances (two preview runs)."""

    def __init__(self, store: dict[str, str], *, fail: bool = False) -> None:
        self._store = store
        self._fail = fail

    async def get(self, name: str) -> str | None:
        if self._fail:
            raise ConnectionError("redis down")
        return self._store.get(name)

    async def set(self, name: str, value: str, *, ex: int | None = None) -> None:
        if self._fail:
            raise ConnectionError("redis down")
        self._store[name] = value


class _FetchCounter(MockAdapter):
    """Counts fetch calls so a result-cache hit (which skips the fetch) is observable."""

    def __init__(self, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.fetches = 0

    async def fetch(self, *args: object, **kwargs: object):  # type: ignore[override]
        self.fetches += 1
        return await super().fetch(*args, **kwargs)  # type: ignore[arg-type]


def _result_cache(store: dict[str, str], *, fail: bool = False) -> ResultCache:
    return ResultCache(
        RedisJsonCache(_SharedFakeRedis(store, fail=fail), namespace="aoi:result"), ttl_s=999
    )


async def test_result_cache_serves_rerun_without_fetching() -> None:
    store: dict[str, str] = {}
    a1 = _FetchCounter()
    out1 = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=a1,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache(store),
    )
    assert a1.fetches > 0
    a2 = _FetchCounter()
    out2 = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=a2,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache(store),
    )
    assert a2.fetches == 0  # every pass was served from the result cache
    assert [p["pass_date"] for p in out2["passes"]] == [p["pass_date"] for p in out1["passes"]]
    assert out2["resolved"] == out1["resolved"]


async def test_result_cache_key_separates_indexes() -> None:
    store: dict[str, str] = {}
    a1 = _FetchCounter()
    await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=a1,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache(store),
    )
    a2 = _FetchCounter()
    await _analyse_aoi_series(
        _GEOM,
        "savi",
        "backfill",
        None,
        6,
        adapter=a2,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache(store),
    )
    assert a2.fetches > 0  # different index -> different key -> recomputed


async def test_result_cache_key_separates_geometries() -> None:
    store: dict[str, str] = {}
    a1 = _FetchCounter()
    await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=a1,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache(store),
    )
    a2 = _FetchCounter()
    await _analyse_aoi_series(
        _GEOM2,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=a2,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache(store),
    )
    assert a2.fetches > 0  # different AOI -> different geometry hash -> recomputed


async def test_result_cache_fails_open_and_still_computes() -> None:
    a = _FetchCounter()
    out = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=a,
        backfill_months=18,
        now=_NOW,
        result_cache=_result_cache({}, fail=True),
    )
    assert a.fetches > 0  # cache get/set raise -> fail open -> normal compute
    assert out["status"] == "ok"


async def test_analyse_aoi_series_emits_telemetry() -> None:
    adapter = _adapter()
    with structlog.testing.capture_logs() as caps:
        await _analyse_aoi_series(
            _GEOM,
            "ndvi",
            "backfill",
            None,
            6,
            adapter=adapter,
            backfill_months=18,
            now=_NOW,
        )

    events = [e for e in caps if e.get("event") == "aoi.series.complete"]
    assert len(events) == 1
    ev = events[0]
    assert ev["index"] == "ndvi"
    assert ev["mode"] == "backfill"
    assert "requested" in ev
    assert "resolved" in ev
    assert "n_passes" in ev
    assert "wall_clock_s" in ev
    assert "result_cache_hits" in ev
    assert "result_cache_misses" in ev


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


# --------------------------------------------------------------------------- multi engine / api


async def test_analyse_aoi_series_multi_dates() -> None:
    from services.worker.tasks.analysis import _analyse_aoi_series_multi

    adapter = _adapter()
    d_min = date(2025, 1, 1)
    d_on_grid = d_min + timedelta(days=3)
    d_off_grid = d_min

    out = await _analyse_aoi_series_multi(
        _GEOM,
        ["ndvi", "savi"],
        "dates",
        [d_off_grid.isoformat(), d_on_grid.isoformat()],
        None,
        adapter=adapter,
    )
    assert out["status"] == "ok"
    assert out["mode"] == "dates"
    assert set(out["indices"].keys()) == {"ndvi", "savi"}

    # Parity check: NDVI in multi matches NDVI in single
    single_ndvi = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "dates",
        [d_off_grid.isoformat(), d_on_grid.isoformat()],
        None,
        adapter=adapter,
    )
    assert out["indices"]["ndvi"]["passes"] == single_ndvi["passes"]


async def test_analyse_aoi_series_multi_backfill() -> None:
    from services.worker.tasks.analysis import _analyse_aoi_series_multi

    adapter = _adapter()
    now = datetime(2025, 6, 15, tzinfo=UTC)
    out = await _analyse_aoi_series_multi(
        _GEOM,
        ["ndvi", "savi"],
        "backfill",
        None,
        6,
        adapter=adapter,
        backfill_months=18,
        now=now,
    )
    assert out["status"] == "ok"
    assert out["mode"] == "backfill"
    assert set(out["indices"].keys()) == {"ndvi", "savi"}

    # Parity check
    single_ndvi = await _analyse_aoi_series(
        _GEOM,
        "ndvi",
        "backfill",
        None,
        6,
        adapter=adapter,
        backfill_months=18,
        now=now,
    )
    assert out["indices"]["ndvi"]["passes"] == single_ndvi["passes"]


async def test_endpoint_enqueues_multi_dates_job(monkeypatch) -> None:
    import services.worker.tasks as tasks

    captured: dict = {}
    monkeypatch.setattr(
        tasks.analyse_aoi_series_multi_task,
        "delay",
        lambda *args: captured.update(args=args) or SimpleNamespace(id="job-multi-abc"),
    )
    req = AOISeriesRequest(
        geometry=_GEOM,
        indices=["ndvi", "savi"],
        mode="dates",
        dates=[date(2025, 1, 15), date(2025, 1, 20)],
    )
    out = await analyse_aoi_series_endpoint(req, _ANALYST)
    assert out == {"job_id": "job-multi-abc", "state": "queued"}
    assert captured["args"] == (
        _GEOM,
        ["ndvi", "savi"],
        "dates",
        ["2025-01-15", "2025-01-20"],
        None,
    )


async def test_endpoint_enqueues_multi_backfill_job(monkeypatch) -> None:
    import services.worker.tasks as tasks

    captured: dict = {}
    monkeypatch.setattr(
        tasks.analyse_aoi_series_multi_task,
        "delay",
        lambda *args: captured.update(args=args) or SimpleNamespace(id="job-multi-bf"),
    )
    req = AOISeriesRequest(geometry=_GEOM, indices=["ndvi", "savi"], mode="backfill", months=6)
    out = await analyse_aoi_series_endpoint(req, _ANALYST)
    assert out["state"] == "queued"
    assert captured["args"] == (_GEOM, ["ndvi", "savi"], "backfill", None, 6)


async def test_endpoint_rejects_missing_both_index_and_indices() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="provide exactly one of"):
        AOISeriesRequest(geometry=_GEOM, mode="backfill", months=6)


async def test_endpoint_rejects_providing_both_index_and_indices() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="provide exactly one of"):
        AOISeriesRequest(
            geometry=_GEOM, index="ndvi", indices=["ndvi", "savi"], mode="backfill", months=6
        )


async def test_endpoint_rejects_empty_indices() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="must be non-empty"):
        AOISeriesRequest(geometry=_GEOM, indices=[], mode="backfill", months=6)


def test_job_status_done_multi(monkeypatch) -> None:
    payload = {
        "status": "ok",
        "mode": "dates",
        "indices": {"ndvi": {"status": "ok", "index": "ndvi", "passes": []}},
    }
    _patch_async_result(monkeypatch, state="SUCCESS", result=payload)
    assert _job_status("j") == {"job_id": "j", "state": "done", "result": payload}
