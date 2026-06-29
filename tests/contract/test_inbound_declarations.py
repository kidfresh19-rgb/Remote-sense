"""Contract pin for the CANDIDATE gateway INBOUND declarations wire shape (gw-inbound/v1).

Ward Watch reads declared crop mix, planting window, drone refs, and the identity join key from the
gateway (additive, ADR 0013). That read does not appear in our served OpenAPI - it is us calling the
gateway - so it is pinned here, not by the openapi diff. The contract is a tolerant candidate
(backlog 0026): unknown fields are ignored and missing values are absent, never a hard failure. See
``CONTRACT.md`` and ``contract/fixtures/gateway_inbound_declarations.example.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from rs_sync import (
    GATEWAY_INBOUND_CONTRACT_VERSION,
    DeclarationsQuery,
    DeclaredCrop,
    DroneReference,
    HouseholdDeclaration,
    HouseholdDeclarationBatch,
    HttpGatewayPort,
    PlantingDeclaration,
    PlotDeclaration,
    RecordingGatewayPort,
)

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "contract"
    / "fixtures"
    / "gateway_inbound_declarations.example.json"
)

_INBOUND_MODELS = (
    DeclaredCrop,
    PlantingDeclaration,
    DroneReference,
    PlotDeclaration,
    HouseholdDeclaration,
    HouseholdDeclarationBatch,
    DeclarationsQuery,
)

# Geometry never travels on the inbound contract either (invariant 6): plot geometry comes from
# enrollment / the proxy-AOI primitive, not from the gateway declarations.
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


def test_candidate_version_is_pinned() -> None:
    assert GATEWAY_INBOUND_CONTRACT_VERSION == "gw-inbound/v1"
    assert HouseholdDeclarationBatch().contract_version == "gw-inbound/v1"


@pytest.mark.parametrize("model", _INBOUND_MODELS)
def test_no_geometry_on_inbound_models(model: type[BaseModel]) -> None:
    for name in model.model_fields:
        offenders = [t for t in _GEO_TERMS if t in name.lower()]
        assert not offenders, (
            f"{model.__name__}.{name}: declarations carry no geometry (invariant 6)"
        )


def test_example_fixture_parses() -> None:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    batch = HouseholdDeclarationBatch.model_validate(raw)

    assert batch.contract_version == GATEWAY_INBOUND_CONTRACT_VERSION
    assert [d.canonical_household_id for d in batch.declarations] == ["HH-1001", "HH-1002"]

    full = batch.declarations[0]
    assert len(full.plots) == 2
    assert [c.crop for c in full.plots[0].crop_mix] == ["maize", "cowpea"]
    assert full.plots[0].crop_mix[0].weight_pct == 60.0
    assert full.plots[0].planting is not None
    assert full.plots[0].drone_refs[0].provider == "agritrack-drone"


def test_unknown_fields_are_ignored() -> None:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    batch = HouseholdDeclarationBatch.model_validate(raw)

    # The fixture sprinkles unknown_gateway_field at batch/household/plot/crop levels; none survive.
    assert "unknown_gateway_field" not in batch.model_dump()
    assert "unknown_gateway_field" not in batch.declarations[0].model_dump()
    assert "unknown_gateway_field" not in batch.declarations[0].plots[0].model_dump()
    assert "unknown_gateway_field" not in batch.declarations[0].plots[0].crop_mix[0].model_dump()


def test_null_cropmix_and_missing_planting_are_tolerated() -> None:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    sparse = HouseholdDeclarationBatch.model_validate(raw).declarations[1]

    assert sparse.village is None
    assert sparse.plots[0].crop_mix == []
    assert sparse.plots[0].planting is None
    assert sparse.plots[0].canonical_plot_id is None


def test_minimal_payloads_validate() -> None:
    # A household with only the join key, and an entirely default plot, must both parse.
    hh = HouseholdDeclaration.model_validate({"canonical_household_id": "HH-9"})
    assert hh.plots == []
    plot = PlotDeclaration.model_validate({})
    assert plot.crop_mix == [] and plot.planting is None and plot.drone_refs == []


async def test_recording_adapter_returns_synthetic_zero_network() -> None:
    batch = await RecordingGatewayPort().fetch_household_declarations(DeclarationsQuery())
    ids = [d.canonical_household_id for d in batch.declarations]
    assert ids == ["HH-1001", "HH-1002"]


async def test_recording_adapter_filters_by_query() -> None:
    port = RecordingGatewayPort()

    by_ward = await port.fetch_household_declarations(DeclarationsQuery(ward_name="Ward 7"))
    assert len(by_ward.declarations) == 2

    by_id = await port.fetch_household_declarations(
        DeclarationsQuery(canonical_household_ids=["HH-1001"])
    )
    assert [d.canonical_household_id for d in by_id.declarations] == ["HH-1001"]

    none = await port.fetch_household_declarations(DeclarationsQuery(ward_name="Nowhere"))
    assert none.declarations == []


async def test_recording_adapter_accepts_injected_batch() -> None:
    pinned = HouseholdDeclarationBatch(
        declarations=[HouseholdDeclaration(canonical_household_id="HH-X")]
    )
    port = RecordingGatewayPort(declarations=pinned)
    batch = await port.fetch_household_declarations(DeclarationsQuery())
    assert [d.canonical_household_id for d in batch.declarations] == ["HH-X"]


async def test_push_only_adapter_does_not_expose_declarations() -> None:
    port = HttpGatewayPort("https://gw.example/push", "bearer-token")
    with pytest.raises(NotImplementedError):
        await port.fetch_household_declarations(DeclarationsQuery())
