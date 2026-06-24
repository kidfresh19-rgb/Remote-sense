"""Unit tests for the all-passes orthophoto bundle task + its Celery routing (orthophoto plan P4).

No broker, no MinIO, no CDSE: the store and the per-pass render are faked, so the cache-reuse /
render-and-cache / zip-assembly / progress logic runs in-process on a bare host. The task is driven
through `.apply(throw=False)` so eager execution captures the result without touching a backend.
"""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from rs_core.storage import aoi_rgb_cog_key

import services.worker.tasks.bundle as bundle_mod
from services.worker.tasks.bundle import bundle_aoi_orthophotos_task

_GEOM = {
    "type": "Polygon",
    "coordinates": [
        [[31.0, -17.8], [31.01, -17.8], [31.01, -17.81], [31.0, -17.81], [31.0, -17.8]]
    ],
}
_HASH = "geomhash"
_BUNDLE_KEY = "aoi_preview/bundle/B1.zip"


class _FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def put(self, key: str, data: bytes, *, content_type: str = "image/tiff") -> None:
        self.objects[key] = data


def _wire(monkeypatch, store: _FakeStore) -> list[dict]:
    """Point the bundle task at a fake store + adapter + render, and capture progress meta so
    nothing reaches a Celery backend."""
    progress: list[dict] = []
    monkeypatch.setattr(bundle_mod, "get_settings", lambda: object())
    monkeypatch.setattr(bundle_mod, "cog_store_from_settings", lambda _s: store)
    monkeypatch.setattr(bundle_mod, "S3CogStore", lambda _s: store)
    monkeypatch.setattr(bundle_mod, "get_access_adapter", lambda _s: object())
    monkeypatch.setattr(bundle_mod, "canonical_geometry_hash", lambda _g: _HASH)

    async def _fake_render(_adapter, scene_id, _geometry, _pass_date):
        return b"RENDERED:" + scene_id.encode()

    monkeypatch.setattr(bundle_mod, "_render_clipped_rgb_cog", _fake_render)
    monkeypatch.setattr(
        bundle_aoi_orthophotos_task,
        "update_state",
        lambda **kwargs: progress.append(kwargs.get("meta", {})),
    )
    return progress


def _run(passes: list[list[str]]):
    return bundle_aoi_orthophotos_task.apply(args=[_GEOM, passes, _BUNDLE_KEY], throw=False)


def test_bundle_reuses_cache_and_renders_missing(monkeypatch) -> None:
    store = _FakeStore()
    # S2A already has a cached COG; S2B is missing and must be rendered, then written back.
    store.objects[aoi_rgb_cog_key("S2A", _HASH)] = b"CACHED:S2A"
    progress = _wire(monkeypatch, store)

    result = _run([["S2A", "2024-10-01"], ["S2B", "2024-10-11"]])
    assert result.successful(), result.result
    assert result.result["passes"] == 2
    assert result.result["skipped"] == 0

    # The missing pass was rendered AND cached (so a later single-pass download is a cache hit).
    assert store.objects[aoi_rgb_cog_key("S2B", _HASH)] == b"RENDERED:S2B"

    with ZipFile(BytesIO(store.objects[_BUNDLE_KEY])) as zf:
        assert set(zf.namelist()) == {"rgb_S2A_2024-10-01.tif", "rgb_S2B_2024-10-11.tif"}
        assert zf.read("rgb_S2A_2024-10-01.tif") == b"CACHED:S2A"  # reused, not re-rendered
        assert zf.read("rgb_S2B_2024-10-11.tif") == b"RENDERED:S2B"

    assert [m.get("done") for m in progress] == [1, 2]
    assert all(m.get("total") == 2 for m in progress)


def test_bundle_skips_failed_pass_but_zips_the_rest(monkeypatch) -> None:
    store = _FakeStore()
    _wire(monkeypatch, store)

    async def _render_one_fails(_adapter, scene_id, _geometry, _pass_date):
        if scene_id == "BAD":
            raise ValueError("scene not found")
        return b"OK:" + scene_id.encode()

    monkeypatch.setattr(bundle_mod, "_render_clipped_rgb_cog", _render_one_fails)

    result = _run([["GOOD", "2024-10-01"], ["BAD", "2024-10-11"]])
    assert result.successful(), result.result
    assert result.result["passes"] == 1
    assert result.result["skipped"] == 1
    with ZipFile(BytesIO(store.objects[_BUNDLE_KEY])) as zf:
        assert zf.namelist() == ["rgb_GOOD_2024-10-01.tif"]


def test_bundle_all_passes_failed_raises(monkeypatch) -> None:
    store = _FakeStore()
    _wire(monkeypatch, store)

    async def _all_fail(_adapter, scene_id, _geometry, _pass_date):
        raise ValueError("nope")

    monkeypatch.setattr(bundle_mod, "_render_clipped_rgb_cog", _all_fail)

    result = _run([["X", "2024-10-01"]])
    assert not result.successful()
    assert "no orthophotos" in str(result.result)
    assert _BUNDLE_KEY not in store.objects  # nothing written when there is nothing to zip


def test_bundle_no_store_raises(monkeypatch) -> None:
    store = _FakeStore()
    _wire(monkeypatch, store)
    monkeypatch.setattr(bundle_mod, "cog_store_from_settings", lambda _s: None)

    result = _run([["X", "2024-10-01"]])
    assert not result.successful()
    assert "object storage" in str(result.result)


def test_bundle_task_routes_to_bulk_not_interactive() -> None:
    """The bundle stays on the default bulk `celery` queue; the exact-name route must win over the
    `analysis.*` glob, or a dozens-of-COGs render would land on the interactive preview lane."""
    from services.worker.celery_app import celery

    def _queue(name: str) -> str | None:
        route = celery.amqp.router.route({}, name)
        q = route.get("queue") if route else None
        return getattr(q, "name", q)

    assert _queue("analysis.bundle_aoi_orthophotos") == "celery"
    assert _queue("analysis.analyse_aoi") == "interactive"
