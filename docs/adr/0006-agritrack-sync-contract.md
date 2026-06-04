# ADR 0006 — The AgriTrack sync contract

- Status: accepted
- Date: 2026-06-04
- Phase: L1 ingestion + L7 sync (Phases 1 and 6)
- Confirms: the two parked `# ⚑ CONFIRM` decisions — the gateway push wire format (`rs_sync`) and the
  DI-1 ingestion onboarding contract (`rs_core/schemas.py`).

## Context

remote-sense is the satellite backbone behind the **AgriTrack** farmer app. Until now the gateway
wire format on both edges was a sensible-default placeholder behind ports (invariant 1), marked
`⚑ CONFIRM` and built so the system worked end to end before the contract was pinned. The AgriTrack
team has now provided the full reconciliation contract, so the placeholders are confirmed and the
markers come down (CLAUDE.md §4: parked decisions stay flagged until the user confirms).

AgriTrack is an Apache/PHP app. Four flows make up the contract:

1. **AgriTrack → us:** `POST /api/v1/mobile/sync` — a farmer with farms, fields and **sub-plots**,
   each carrying a GeoJSON polygon. This is the identity + geometry the gateway owns (invariant 6);
   we ingest a read-only copy.
2. **us → AgriTrack:** `POST /integrations/satellite/results` (header `X-Api-Key`) — one record per
   `(farmId, fieldId, subPlotId, analysisDate)` with flattened metrics and an interpretation block.
   Deduplicated on `extId`. No geometry (invariant 6).
3. **us → AgriTrack (pull):** `GET /api/v1/mobile/data` — we expose the same records for them to
   pull when a push could not be delivered.
4. **we read (optional):** their `GET /farms` and `GET /boundaries/farms/{id}/fields`.

## Decision

### 1. AgriTrack IDs are encoded in the canonical join keys (no new geometry model)

The contract IDs map onto our existing `canonical_farm_id` / `canonical_field_id` (invariant 6's
cross-system join keys) losslessly, so no schema migration is needed to onboard:

| AgriTrack | Canonical key we store | Scope |
|---|---|---|
| `farm_id` `"2"` | `canonical_farm_id = "2"` | farm |
| `field_id` `"4"` | `canonical_field_id = "4"` | field |
| `sub_plots[].plot_id` `"1"` under field `"4"` | `canonical_field_id = "4.1"` | sub_plot |

On the outbound side these decode back to integers: `"4"` → `fieldId=4, subPlotId=null, scope=field`;
`"4.1"` → `fieldId=4, subPlotId=1, scope=sub_plot`. The farmer (`agritrack_id`, name, email) is
context only; it is not in the outbound contract and is not stored in v1.

### 2. Field AND sub-plot are both analysis units

When a field carries sub-plots we ingest the field polygon **and** each sub-plot polygon as separate
`Field` rows (they nest within the farm, DI-3). This reuses the entire pipeline (collection,
analysis, COG, interpretation, workspace) unchanged — a sub-plot is just another `Field`. Results
are pushed at both `field` and `sub_plot` scope.

### 3. Index → metric mapping

`ndvi_*`←NDVI, `evi_mean`←EVI2, **`ndwi_mean`←NDMI** (our B08/B11 moisture index; named `ndwi` on
their side), `cloud_cover_pct`←`(1 - clear_fraction) * 100`. `health_score` and `classification`
(healthy|moderate|stressed|critical) derive from the `rs_interpret` index bands; `interpretation`
(`stress_level`, `anomalies`) comes from the stored `Interpretation` and `rs_core/alerts.py`. All of
this AgriTrack-specific shaping lives in the outbound adapter, not in the engine.

### 4. One API key, both directions; everything behind the two ports

We reuse the single AgriTrack API key (`RS_AGRITRACK_API_KEY`, the `atk_…` value): presented as
`X-Api-Key` when we call their `/results`, and required on inbound `/api/v1/mobile/*` calls
(constant-time compare). All AgriTrack URL/auth/schema knowledge stays behind adapters: the inbound
translation (their payload → `FarmIn`) and the outbound `AgriTrackGatewayPort` (a `GatewayPort`
adapter selected by `RS_GATEWAY_ADAPTER`). Secrets come from env only (invariant 8).

## Consequences

- Inbound ingestion and the outbound push are now real, not placeholders; the `⚑ CONFIRM` markers in
  `rs_sync/payload.py`, `rs_sync/adapters.py` and `rs_core/schemas.py` are removed.
- No migration is required to onboard farms; an explicit sub-plot tier or stored farmer record can be
  added later if a need appears, but the canonical-ID encoding is sufficient for the contract.
- Geometry still never leaves (invariant 6): it flows in on `/sync` and is absent from every outbound
  record.
- The single shared key is the simplest option but couples the two directions; it was chosen at the
  owner's request. Rotating it (it has been exposed in chat) is a one-value change in `.env`.
