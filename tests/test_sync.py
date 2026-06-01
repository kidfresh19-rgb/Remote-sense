"""No-infra tests for rs_sync (L7): the additive gateway payload carries provenance but never
geometry, the idempotency key is deterministic and order-independent, the CSV export, and the
gateway adapters (recording dedup + http with a faked client, incl. the retry-exhaustion path)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from rs_sync import (
    HttpGatewayPort,
    IndexResult,
    RecordingGatewayPort,
    analyses_to_csv,
    build_payload,
)


def _result(*, index: str = "ndvi", field: str | None = "fld-1", mean: float = 0.6) -> IndexResult:
    return IndexResult(
        canonical_field_id=field,
        index_name=index,
        pass_date=date(2025, 1, 15),
        mean=mean,
        min=0.1,
        max=0.8,
        std=0.05,
        p10=0.3,
        p90=0.75,
        clear_fraction=0.9,
        confidence="high",
        resolution_m=10.0,
        formula_version="1",
        provider="cdse",
        provider_scene_id="S2_X",
        processing_mode="windowed_cog",
    )


def test_build_payload_is_additive_and_carries_no_geometry() -> None:
    payload = build_payload(
        "FARM-1",
        [_result(), _result(index="ndre")],
        generated_at=datetime(2025, 1, 16, tzinfo=UTC),
    )
    assert payload.canonical_farm_id == "FARM-1"  # additive, keyed by canonical farm id
    assert payload.payload_version == "gw/v1"
    assert len(payload.results) == 2
    blob = payload.model_dump_json()
    assert "geometry" not in blob
    assert "boundary" not in blob
    assert "coordinates" not in blob


def test_idempotency_key_is_deterministic_and_order_independent() -> None:
    a = build_payload("FARM-1", [_result(index="ndvi"), _result(index="ndre")])
    b = build_payload("FARM-1", [_result(index="ndre"), _result(index="ndvi")])
    assert a.idempotency_key == b.idempotency_key  # set identity, order-independent
    c = build_payload("FARM-1", [_result(index="ndvi")])
    assert c.idempotency_key != a.idempotency_key  # a different result set -> a different key


def test_analyses_to_csv_has_header_values_and_no_geometry() -> None:
    csv_text = analyses_to_csv([_result(mean=0.62)])
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("canonical_field_id,index_name,pass_date")
    assert "ndvi" in lines[1]
    assert "0.62" in lines[1]
    assert "geometry" not in csv_text


async def test_recording_gateway_dedupes_by_idempotency_key() -> None:
    port = RecordingGatewayPort()
    payload = build_payload("FARM-1", [_result()])
    first = await port.push(payload)
    second = await port.push(payload)  # same key -> duplicate, not re-recorded
    assert first.ok
    assert first.status == "ok"
    assert second.status == "duplicate"
    assert len(port.pushed) == 1


async def test_http_gateway_posts_with_bearer_and_idempotency_header() -> None:
    captured: dict = {}

    class _Resp:
        status_code = 202

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def post(self, url, **kwargs):
            captured.update(url=url, json=kwargs["json"], headers=kwargs["headers"])
            return _Resp()

    port = HttpGatewayPort("https://gw.example/push", "secret-token", client=_Client())
    payload = build_payload("FARM-1", [_result()])
    result = await port.push(payload)

    assert result.ok
    assert result.status == "202"
    assert captured["url"] == "https://gw.example/push"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["Idempotency-Key"] == payload.idempotency_key
    assert captured["json"]["canonical_farm_id"] == "FARM-1"
    assert "geometry" not in captured["json"]


async def test_http_gateway_returns_error_after_retries() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls = 0

        async def post(self, url, **kwargs):
            self.calls += 1
            raise httpx.ConnectError("boom")

    client = _Client()
    port = HttpGatewayPort(
        "https://gw.example/push", "t", client=client, max_attempts=2, backoff=0.0
    )
    result = await port.push(build_payload("FARM-1", [_result()]))

    assert not result.ok
    assert result.status == "error"
    assert client.calls == 2  # retried up to max_attempts, then dead-letters


def test_http_gateway_requires_url() -> None:
    with pytest.raises(ValueError, match="gateway_push_url"):
        HttpGatewayPort("", "token")
