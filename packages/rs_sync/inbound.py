"""Gateway INBOUND declarations contract (L7) - the read-direction mirror of `payload.py`.

Ward Watch is an additive consumer (ADR 0013): the gateway stays identity authority and exposes,
read-only, what an AGRITEX officer captured at enrollment - the declared crop mix, the planting
window, drone-imagery references, and the canonical identity join key. We fetch those declarations
through the `GatewayPort` (invariant 1: all gateway I/O behind the port) and never write them back.

CANDIDATE v1 (`gw-inbound/v1`): the real gateway field names, units, nullability, and the GET path
are not yet confirmed, so every model is tolerant - `extra="ignore"` and nullable wherever the
gateway may not hold a value - and the candidate field names are marked `# CONFIRM`. The receiver-
tolerance rule applies: an unknown or empty value is treated as absent, never a hard failure. This
module is pure wire shape; it does not validate against the crop vocabulary or bucket planting
windows (that is ingestion's job, 0031, via `rs_core.cropmix` / `rs_core.strata`), and it carries
NO geometry (plot geometry comes from enrollment / the proxy-AOI primitive, not this contract).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

# CANDIDATE contract version. Bumped (additively) when the gateway team confirms real field names;
# the value travels on every batch so a consumer can tell which revision it parsed.
GATEWAY_INBOUND_CONTRACT_VERSION = "gw-inbound/v1"


class DeclaredCrop(BaseModel):
    """One crop's share of a plot's intercrop mix. Maps to `rs_core.models.CropMixEntry`
    (crop, weight_pct). `weight_pct` is nullable because the gateway may hold a bare crop list with
    no shares; 0031 resolves and validates the mix (weights in (0, 100], summing ~100)."""

    model_config = ConfigDict(extra="ignore")

    crop: str  # CONFIRM gateway field name + vocabulary (canonical code vs free text)
    weight_pct: float | None = None


class PlantingDeclaration(BaseModel):
    """A plot's declared planting. We keep the RAW declared date and bucket it ourselves
    (`rs_core.strata.bucket_planting_window`, resolution honesty); `declared_window` is an optional
    advisory label the gateway may already hold and is never authoritative over our bucketing."""

    model_config = ConfigDict(extra="ignore")

    planting_date: date | None = None  # CONFIRM gateway field name + format (ISO date assumed)
    declared_window: str | None = None


class DroneReference(BaseModel):
    """An opaque pointer to drone imagery held by a separate gateway app. We carry the reference for
    traceability only; v1 never fetches or processes it here."""

    model_config = ConfigDict(extra="ignore")

    ref: str  # CONFIRM whether this is an id or a URI
    captured_at: date | None = None
    provider: str | None = None
    sensor: str | None = None


class PlotDeclaration(BaseModel):
    """One intercropped plot's declarations. `canonical_plot_id` is the gateway-assigned id (null
    until enrollment sync assigns it); `client_uuid` is the offline client's own stable id and the
    fall-back join when no canonical id exists yet (matches `rs_core.models.Plot`)."""

    model_config = ConfigDict(extra="ignore")

    canonical_plot_id: str | None = None
    client_uuid: str | None = None
    crop_mix: list[DeclaredCrop] = []
    planting: PlantingDeclaration | None = None
    drone_refs: list[DroneReference] = []


class HouseholdDeclaration(BaseModel):
    """A household's declarations, keyed by the cross-system identity join key
    `canonical_household_id` (-> `rs_core.models.Household.canonical_household_id`). `ward_name` and
    `village` are advisory echoes; ward assignment is ours (centroid-in-boundary, ADR 0010)."""

    model_config = ConfigDict(extra="ignore")

    canonical_household_id: str  # CONFIRM gateway field name for the join key
    client_uuid: str | None = None
    ward_name: str | None = None
    village: str | None = None
    plots: list[PlotDeclaration] = []


class HouseholdDeclarationBatch(BaseModel):
    """A page of household declarations returned by one fetch. `contract_version` stamps the
    candidate revision the producer emitted so a consumer can detect a swap."""

    model_config = ConfigDict(extra="ignore")

    contract_version: str = GATEWAY_INBOUND_CONTRACT_VERSION
    declarations: list[HouseholdDeclaration] = []


class DeclarationsQuery(BaseModel):
    """What we ask the gateway for, read-only. All filters are optional; an empty query asks for
    everything the caller is scoped to. `since` enables incremental pulls once the gateway supports
    it (advisory in v1)."""

    model_config = ConfigDict(extra="ignore")

    ward_name: str | None = None
    canonical_household_ids: list[str] | None = None
    since: datetime | None = None


def synthetic_declarations() -> HouseholdDeclarationBatch:
    """A deterministic two-household batch for the mock adapter and tests (zero network). The first
    household is fully populated (two plots, an intercrop mix summing to 100, a planting date, a
    drone reference); the second is deliberately sparse (null crop mix, missing planting, no drone)
    to exercise the receiver-tolerance path. Crop names are plain wire strings, not validated."""

    full = HouseholdDeclaration(
        canonical_household_id="HH-1001",
        client_uuid="11111111-1111-4111-8111-111111111111",
        ward_name="Ward 7",
        village="Chikomba",
        plots=[
            PlotDeclaration(
                canonical_plot_id="PL-1001-A",
                client_uuid="aaaaaaaa-1111-4111-8111-111111111111",
                crop_mix=[
                    DeclaredCrop(crop="maize", weight_pct=60.0),
                    DeclaredCrop(crop="cowpea", weight_pct=40.0),
                ],
                planting=PlantingDeclaration(
                    planting_date=date(2025, 11, 20), declared_window="early"
                ),
                drone_refs=[
                    DroneReference(
                        ref="drone://flight/2026-01-14/HH-1001",
                        captured_at=date(2026, 1, 14),
                        provider="agritrack-drone",
                        sensor="rgb",
                    )
                ],
            ),
            PlotDeclaration(
                canonical_plot_id="PL-1001-B",
                client_uuid="bbbbbbbb-1111-4111-8111-111111111111",
                crop_mix=[DeclaredCrop(crop="groundnut", weight_pct=100.0)],
                planting=PlantingDeclaration(planting_date=date(2025, 12, 5)),
            ),
        ],
    )
    sparse = HouseholdDeclaration(
        canonical_household_id="HH-1002",
        client_uuid="22222222-2222-4222-8222-222222222222",
        ward_name="Ward 7",
        plots=[
            PlotDeclaration(
                client_uuid="cccccccc-2222-4222-8222-222222222222",
                crop_mix=[],
                planting=None,
            )
        ],
    )
    return HouseholdDeclarationBatch(declarations=[full, sparse])
