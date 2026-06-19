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

`ndvi_*`←NDVI, `evi_mean`←EVI2, `savi_mean`←SAVI, `ndre_mean`←NDRE, **`ndwi_mean`←NDMI** (our
B08/B11 moisture index; named `ndwi` on their side), `cloud_cover_pct`←`(1 - clear_fraction) * 100`. `health_score` and `classification`
(healthy|moderate|stressed|critical) derive from the `rs_interpret` index bands. The `interpretation`
block carries `stress_level` (from the NDVI vigour band), `notes` (the agronomist's reviewed
narrative), and `anomalies` (reserved for `rs_core/alerts.py`). All of this AgriTrack-specific
shaping lives in the outbound adapter, not in the engine.

> Amended 2026-06-19: `savi_mean`←SAVI and `ndre_mean`←NDRE were added to the metrics block. Both
> indices were already computed, stored, and shown in the workspace, but they were silently dropped
> at the outbound adapter because the wire schema had no field for them, so only NDVI/EVI2/NDMI
> reached AgriTrack. AgriTrack confirmed the receiving fields. The change is additive (new optional
> fields; no existing key renamed or removed), so the frozen-contract diff gate stays green and
> re-pushes stay idempotent. Mean only, matching EVI2/NDMI; NDVI keeps its min/max as the
> classification driver.

### 3a. The published narrative is gated on review (risk #6 extended to the wire)

`interpretation.notes` carries an agronomic read **only when it has been published** by an
agronomist (`Interpretation.published`, set through `PATCH /fields/{id}/interpretations/{iid}`,
`publish` permission). An unreviewed or withheld draft never leaves — the same never-auto-publish
rule that governs the workspace now governs the gateway. The published narratives travel on the
canonical `GatewayPayload` (`interpretations: [PublishedNarrative]`, geometry-free) for both the
push and the `/api/v1/mobile/data` pull, so the two never diverge. Publishing or editing a narrative
changes the payload's idempotency key (a narrative signature is folded in), so a read published
after its analyses were already pushed is re-delivered additively; `extId` dedupes on AgriTrack's
side and the `SyncOutbox` on ours (R-2).

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

## Push reliability (operational hardening)

The wire contract above is fixed; how the push *behaves on failure* was hardened without changing it
(all behind `GatewayPort`, so no architecture change):

- **Retry classification.** A push retries only on transient failures - transport/timeout errors and
  HTTP 429/5xx - with jittered exponential backoff. A permanent 4xx (bad `X-Api-Key` -> 401, wrong
  URL -> 404, malformed record -> 422) fails fast instead of burning four retries per record. The
  policy is one shared module (`rs_sync/resilience.py`) used by both the AgriTrack and generic-HTTP
  adapters.
- **Always a terminal state.** Every publish lands `published` or `dead_letter` in the `SyncOutbox`,
  never crashing the task: the adapter converts a non-integer farm id and HTTP/transport failures to
  a dead-letter, and the publisher has a catch-all backstop. The workspace therefore never polls a
  push that can't resolve.
- **Fail fast on misconfiguration.** `POST /farms/{id}/publish` returns 503 with the reason when a
  real-push adapter (`agritrack`/`http`) is selected but its URL or key is missing, a placeholder, or
  scheme-less - rather than enqueuing a doomed task. The `recording` dry-run stays valid and is
  surfaced as `dry_run=true` so it is never mistaken for a real delivery.
- **Actionable diagnostics.** A dead-letter records the status code plus a truncated response-body
  snippet (e.g. an AgriTrack JSON error, or an `ERR_NGROK_3200 ... is offline` page), the failure
  ratio (`3/10 records failed`), and a structured `gateway.push.*` log line. None of this changes the
  bytes on the wire - `extId`, `farmId/fieldId/subPlotId`, `analysisDate`, and the metric mapping are
  untouched, so re-pushes still additively update the same AgriTrack records.
