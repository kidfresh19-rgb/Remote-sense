"""Contract pin for the outbound gateway payload (the L7 wire shape).

The push to the gateway / AgriTrack does not appear in the served OpenAPI, so it is pinned here.
Two things are protected: the exact field set of each outbound model (so a removed or renamed
field is caught), and the absolute absence of geometry on any of them (CLAUDE.md invariant 6,
geometry is never returned). See ``CONTRACT.md``.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from rs_sync import (
    PAYLOAD_VERSION,
    GatewayPayload,
    IndexResult,
    PublishedNarrative,
    SatelliteResult,
    SubPlotEntry,
)

_GEO_TERMS = (
    "geom",
    "geometry",
    "boundary",
    "coordinate",
    "polygon",
    "geojson",
    "wkt",
    "latitude",
    "longitude",
)

_OUTBOUND_MODELS = (IndexResult, PublishedNarrative, GatewayPayload, SatelliteResult, SubPlotEntry)


def test_payload_version_is_pinned() -> None:
    assert PAYLOAD_VERSION == "gw/v1"


@pytest.mark.parametrize("model", _OUTBOUND_MODELS)
def test_no_geometry_on_outbound_models(model: type[BaseModel]) -> None:
    for name in model.model_fields:
        lowered = name.lower()
        offenders = [t for t in _GEO_TERMS if t in lowered]
        assert not offenders, f"{model.__name__}.{name}: geometry is never published (invariant 6)"


def test_index_result_fields() -> None:
    assert set(IndexResult.model_fields) == {
        "canonical_field_id",
        "index_name",
        "pass_date",
        "mean",
        "min",
        "max",
        "std",
        "p5",
        "p10",
        "p90",
        "p95",
        "clear_fraction",
        "confidence",
        "resolution_m",
        "formula_version",
        "provider",
        "provider_scene_id",
        "processing_mode",
    }


def test_published_narrative_fields() -> None:
    assert set(PublishedNarrative.model_fields) == {
        "canonical_field_id",
        "pass_date",
        "narrative",
    }


def test_gateway_payload_fields() -> None:
    assert set(GatewayPayload.model_fields) == {
        "payload_version",
        "canonical_farm_id",
        "generated_at",
        "results",
        "interpretations",
        "idempotency_key",
    }


def test_satellite_result_fields() -> None:
    assert set(SatelliteResult.model_fields) == {
        "sourceSystem",
        "farmId",
        "fieldId",
        "subPlotId",
        "scope",
        "analysisDate",
        "extId",
        "metrics",
        "interpretation",
        "outputs",
        "subPlots",
    }


def test_sub_plot_entry_fields() -> None:
    assert set(SubPlotEntry.model_fields) == {
        "subPlotId",
        "extId",
        "metrics",
    }
