"""AgriTrack outbound results adapter (ADR 0006). Zero network: the aggregation is a pure transform
and the push is exercised through an injected httpx.MockTransport, so nothing touches AgriTrack."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from rs_sync import AgriTrackGatewayPort, IndexResult, build_payload, to_satellite_results
from rs_sync.agritrack import _decode_field


def _ir(
    field: str | None,
    index: str,
    mean: float | None,
    *,
    pass_date: date = date(2026, 5, 28),
    clear: float = 0.95,
    mn: float | None = None,
    mx: float | None = None,
) -> IndexResult:
    return IndexResult(
        canonical_field_id=field,
        index_name=index,
        pass_date=pass_date,
        mean=mean,
        min=mn,
        max=mx,
        std=None,
        p10=None,
        p90=None,
        clear_fraction=clear,
        confidence="high",
        resolution_m=10.0,
        formula_version="v1",
        provider="cdse",
        provider_scene_id="S2_X",
        processing_mode="windowed_cog",
    )


def test_to_satellite_results_aggregates_field_and_subplot():
    results = [
        _ir("4", "ndvi", 0.62, mn=0.30, mx=0.80, clear=0.95),
        _ir("4", "evi2", 0.55),
        _ir("4", "ndmi", 0.40),
        _ir("4.1", "ndvi", 0.25),
    ]
    recs = to_satellite_results(build_payload("2", results))
    assert [(r.fieldId, r.subPlotId, r.scope) for r in recs] == [
        (4, None, "field"),
        (4, 1, "sub_plot"),
    ]
    field = recs[0]
    assert field.sourceSystem == "satellite"
    assert field.farmId == 2
    assert field.analysisDate == "2026-05-28"
    assert field.metrics.ndvi_mean == 0.62
    assert field.metrics.ndvi_min == 0.30
    assert field.metrics.ndvi_max == 0.80
    assert field.metrics.evi_mean == 0.55
    assert field.metrics.ndwi_mean == 0.40  # NDMI -> ndwi_mean (ADR 0006)
    assert field.metrics.cloud_cover_pct == 5.0  # (1 - 0.95) * 100
    assert field.metrics.classification == "healthy"  # ndvi 0.62 -> vigorous
    assert field.metrics.health_score == 0.62
    assert field.interpretation is not None and field.interpretation.stress_level == "none"
    assert field.extId == "2:4:2026-05-28"

    sub = recs[1]
    assert sub.metrics.classification == "stressed"  # ndvi 0.25 -> sparse
    assert sub.interpretation is not None and sub.interpretation.stress_level == "moderate"
    assert sub.extId == "2:4.1:2026-05-28"


@pytest.mark.parametrize(
    "ndvi,expected",
    [
        (0.85, "healthy"),
        (0.70, "healthy"),
        (0.50, "moderate"),
        (0.30, "stressed"),
        (0.10, "critical"),
    ],
)
def test_classification_maps_ndvi_vigour_band(ndvi: float, expected: str):
    recs = to_satellite_results(build_payload("2", [_ir("4", "ndvi", ndvi)]))
    assert recs[0].metrics.classification == expected


def test_decode_field():
    assert _decode_field("4") == (4, None, "field")
    assert _decode_field("4.1") == (4, 1, "sub_plot")
    assert _decode_field(None) == (None, None, "farm")
    assert _decode_field("derived") == (None, None, "farm")


def test_non_integer_farm_id_raises():
    with pytest.raises(ValueError):
        to_satellite_results(build_payload("not-an-int", [_ir("4", "ndvi", 0.5)]))


async def test_push_posts_one_record_per_field_date_with_api_key():
    captured: list[tuple[str, str | None, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (str(request.url), request.headers.get("X-Api-Key"), json.loads(request.content))
        )
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort("https://agri.example/", "atk_key", client=client)
    payload = build_payload("2", [_ir("4", "ndvi", 0.62), _ir("4", "ndmi", 0.40)])
    result = await port.push(payload)
    await client.aclose()

    assert result.ok
    assert len(captured) == 1  # one (field, date) record
    url, key, body = captured[0]
    assert url == "https://agri.example/integrations/satellite/results"
    assert key == "atk_key"
    assert body["sourceSystem"] == "satellite"
    assert body["farmId"] == 2
    assert body["fieldId"] == 4
    assert body["metrics"]["ndvi_mean"] == 0.62
    assert body["metrics"]["ndwi_mean"] == 0.40


async def test_push_dead_letters_on_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    port = AgriTrackGatewayPort("https://agri.example", "atk_key", client=client, backoff=0.0)
    result = await port.push(build_payload("2", [_ir("4", "ndvi", 0.5)]))
    await client.aclose()

    assert result.ok is False
    assert result.status == "error"


def test_missing_config_raises():
    with pytest.raises(ValueError):
        AgriTrackGatewayPort("", "atk_key")
    with pytest.raises(ValueError):
        AgriTrackGatewayPort("https://agri.example", "")
