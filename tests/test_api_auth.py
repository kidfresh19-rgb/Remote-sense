"""TestClient auth tests for the RBAC-gated endpoints (Phase 7). The JWT layer is unit-tested in
test_auth.py; here we override `get_principal` to exercise the require()/endpoint gating without a
real token and without a DB (401/403 fire before any endpoint body runs). The publish happy path
needs a seeded farm, so it lives in test_workspace_db.py."""

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
            resp = client.post("/farms/FARM-1/publish")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_publish_unauthenticated_is_401() -> None:
    with TestClient(app) as client:  # no override -> real get_principal; no bearer header -> 401
        resp = client.post("/farms/FARM-1/publish")
    assert resp.status_code == 401


def test_workspace_endpoint_requires_auth() -> None:
    # The workspace BFF (L6) is RBAC-gated: no token -> 401 before any DB access.
    with TestClient(app) as client:
        resp = client.get("/farms")
    assert resp.status_code == 401


def test_annotation_write_forbidden_for_viewer() -> None:
    # Posting a field note needs `annotate`; a viewer is gated out (403) before any DB access.
    _as(Principal(subject="viewer", roles=frozenset({Role.VIEWER})))
    field_id = "00000000-0000-0000-0000-000000000001"
    try:
        with TestClient(app) as client:
            resp = client.post(f"/fields/{field_id}/annotations", json={"body": "note"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_annotation_read_requires_auth() -> None:
    field_id = "00000000-0000-0000-0000-000000000001"
    with TestClient(app) as client:  # no override, no bearer -> 401 before any DB access
        resp = client.get(f"/fields/{field_id}/annotations")
    assert resp.status_code == 401


def test_field_collect_forbidden_for_viewer() -> None:
    # Triggering a field backfill needs `run_analysis`; a viewer is gated out (403) before any DB.
    _as(Principal(subject="viewer", roles=frozenset({Role.VIEWER})))
    field_id = "00000000-0000-0000-0000-000000000001"
    try:
        with TestClient(app) as client:
            resp = client.post(f"/fields/{field_id}/collect")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_field_collect_requires_auth() -> None:
    field_id = "00000000-0000-0000-0000-000000000001"
    with TestClient(app) as client:  # no override, no bearer -> 401 before any DB access
        resp = client.post(f"/fields/{field_id}/collect")
    assert resp.status_code == 401


def test_review_interpretation_forbidden_for_viewer() -> None:
    # Publishing/withholding a read needs `publish`; a viewer is gated out (403) before any DB.
    _as(Principal(subject="viewer", roles=frozenset({Role.VIEWER})))
    field_id = "00000000-0000-0000-0000-000000000001"
    interp_id = "00000000-0000-0000-0000-000000000002"
    try:
        with TestClient(app) as client:
            resp = client.patch(
                f"/fields/{field_id}/interpretations/{interp_id}", json={"publish": True}
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_review_queue_forbidden_for_viewer() -> None:
    _as(Principal(subject="viewer", roles=frozenset({Role.VIEWER})))
    try:
        with TestClient(app) as client:
            resp = client.get("/interpretations/review-queue")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_review_queue_requires_auth() -> None:
    with TestClient(app) as client:  # no override, no bearer -> 401 before any DB access
        resp = client.get("/interpretations/review-queue")
    assert resp.status_code == 401


def test_cors_preflight_allows_workspace_origin() -> None:
    # The browser SPA preflights cross-origin calls; without CORS this OPTIONS is a 405 and the
    # browser blocks the real request. Expect the middleware to allow the configured origin.
    with TestClient(app) as client:
        resp = client.options(
            "/farms",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
