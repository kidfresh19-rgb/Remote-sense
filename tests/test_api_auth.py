"""TestClient auth tests for the RBAC-gated endpoints (Phase 7). The JWT layer is unit-tested in
test_auth.py; here we override `get_principal` to exercise the require()/endpoint gating without a
real token and without a DB (the publish endpoint only enqueues)."""

from __future__ import annotations

from rs_core.rbac import Principal, Role
from starlette.testclient import TestClient

from services.api.auth import get_principal
from services.api.main import app


def _as(principal: Principal) -> None:
    app.dependency_overrides[get_principal] = lambda: principal


def test_publish_forbidden_without_publish_permission() -> None:
    _as(Principal(subject="viewer", roles=frozenset({Role.VIEWER})))
    try:
        with TestClient(app) as client:
            resp = client.post("/publish/farm/FARM-1")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_publish_enqueues_for_publisher(monkeypatch) -> None:
    import services.worker.tasks as tasks

    enqueued: dict = {}
    monkeypatch.setattr(tasks.publish_farm_task, "delay", lambda fid: enqueued.update(fid=fid))
    _as(Principal(subject="pub", roles=frozenset({Role.PUBLISHER})))
    try:
        with TestClient(app) as client:
            resp = client.post("/publish/farm/FARM-1")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 202
    assert resp.json()["by"] == "pub"
    assert enqueued["fid"] == "FARM-1"


def test_publish_unauthenticated_is_401() -> None:
    with TestClient(app) as client:  # no override -> real get_principal; no bearer header -> 401
        resp = client.post("/publish/farm/FARM-1")
    assert resp.status_code == 401


def test_workspace_endpoint_requires_auth() -> None:
    # The workspace BFF (L6) is RBAC-gated: no token -> 401 before any DB access.
    with TestClient(app) as client:
        resp = client.get("/farms")
    assert resp.status_code == 401
