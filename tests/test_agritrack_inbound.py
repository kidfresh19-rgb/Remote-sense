"""AgriTrack INBOUND declarations adapter (ADR 0013, backlog 0026).

Zero network: the read-only GET is exercised through an injected httpx.MockTransport, so nothing
touches the gateway. Field names and the path are a CANDIDATE (gw-inbound/v1); these tests pin the
transport behaviour, not the schema."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from rs_sync import (
    AgriTrackGatewayPort,
    DeclarationsQuery,
    synthetic_declarations,
)


def _ok_handler(captured: list[httpx.Request]):
    body = synthetic_declarations().model_dump(mode="json")
    # A field the gateway might add that our models do not know about - must be ignored on parse.
    body["unexpected_envelope_field"] = "ignored"

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=body)

    return handler


async def test_fetch_issues_get_with_api_key() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler(captured)))
    port = AgriTrackGatewayPort("https://agri.example/", "atk_key", client=client)

    batch = await port.fetch_household_declarations(DeclarationsQuery())
    await client.aclose()

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "GET"
    assert str(request.url).startswith("https://agri.example/integrations/households/declarations")
    assert request.headers.get("X-Api-Key") == "atk_key"
    # Read-only: a GET carries no body.
    assert not request.content
    assert [d.canonical_household_id for d in batch.declarations] == ["HH-1001", "HH-1002"]


async def test_fetch_forwards_query_params() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler(captured)))
    port = AgriTrackGatewayPort("https://agri.example", "atk_key", client=client)

    await port.fetch_household_declarations(
        DeclarationsQuery(
            ward_name="Ward 7",
            canonical_household_ids=["HH-1001", "HH-1002"],
            since=datetime(2026, 6, 1, 0, 0, 0),
        )
    )
    await client.aclose()

    params = captured[0].url.params
    assert params.get("ward") == "Ward 7"
    assert params.get_list("household_id") == ["HH-1001", "HH-1002"]
    assert params.get("since") == "2026-06-01T00:00:00"


async def test_fetch_tolerates_unknown_fields() -> None:
    # The handler injects an unexpected envelope field; parsing must still succeed.
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler(captured)))
    port = AgriTrackGatewayPort("https://agri.example", "atk_key", client=client)

    batch = await port.fetch_household_declarations(DeclarationsQuery())
    await client.aclose()

    assert "unexpected_envelope_field" not in batch.model_dump()
    assert batch.contract_version == "gw-inbound/v1"


async def test_fetch_propagates_server_error() -> None:
    # A read failure must surface, not be swallowed into an empty batch (which would read as
    # 'no distressed households'). 5xx is retried then re-raised.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort("https://agri.example", "atk_key", client=client, backoff=0.0)

    with pytest.raises(httpx.HTTPStatusError):
        await port.fetch_household_declarations(DeclarationsQuery())
    await client.aclose()
