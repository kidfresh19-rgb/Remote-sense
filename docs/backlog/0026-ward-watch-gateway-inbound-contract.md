# Backlog 0026 — Ward Watch: gateway inbound data contract

- Status: built 2026-06-29 (candidate `gw-inbound/v1` behind the GatewayPort, on
  `feat/ward-watch-movement-lens`; gateway field names + GET path ⚑ CONFIRM pending the gateway team)
- Type: backend (rs_sync / GatewayPort)
- Parent: PRD 0003 (`docs/prd/0003-ward-watch.md`), Phase 0
- Blocked by: 0025
- Invariants / decisions: §1.1 all gateway I/O behind the GatewayPort; §1.6 inbound is read-only here;
  §1.8 secrets via settings; the frozen OUTBOUND wire is untouched (this is additive INBOUND only).

## Context

Additive extension means new inbound flows from the gateway: declared crop mix, planting window, drone
references, and the identity join key (PRD 0003 §9.4, §12.1). These feed enrollment ingest and crop
verification; the system must tolerate the gateway not having a field yet.

## What to build

- Pydantic v2 models for the inbound payloads (crop-mix matrix, planting window, drone refs, identity
  join key), tolerant: nullable where the gateway may not hold the value.
- A `fetch_household_declarations(...)` method on the GatewayPort, implemented in both the mock adapter
  (synthetic) and the real adapter, read-only.
- Contract-test fixtures for a documented candidate schema.

## Acceptance criteria

- [x] Models validate the candidate payload and ignore unknown fields. (`packages/rs_sync/inbound.py`,
      `tests/contract/test_inbound_declarations.py`)
- [x] Null crop-mix or missing planting window is tolerated, never hard-fails ingest.
- [x] The mock adapter returns synthetic declarations with zero network. (`RecordingGatewayPort`)
- [x] All gateway access stays behind the GatewayPort (no URL/SDK leak outside the adapter). (real GET
      lives only in `AgriTrackGatewayPort`)
- [x] ruff + ruff format + mypy + pytest green.

## Built

- `packages/rs_sync/inbound.py`: tolerant Pydantic v2 wire models (`HouseholdDeclarationBatch` ->
  `HouseholdDeclaration` -> `PlotDeclaration` -> `DeclaredCrop` / `PlantingDeclaration` /
  `DroneReference`) + `DeclarationsQuery`, all `extra="ignore"`, version `gw-inbound/v1`. Geometry-free
  (invariant 6); pure wire (no crop-vocab / bucketing here - that is 0031).
- `GatewayPort.fetch_household_declarations` (read-only, additive, ADR 0013): default raises
  (optional capability); `RecordingGatewayPort` returns a synthetic batch filtered by the query;
  `AgriTrackGatewayPort` issues a read-only GET to a candidate path with the reused `X-Api-Key`.
- Recorded in `CONTRACT.md` (ADDITIVE INBOUND section) +
  `contract/fixtures/gateway_inbound_declarations.example.json`.

## Resolve before merge

- # CONFIRM the real gateway field names, units, and nullability for crop mix, planting window, and
  drone references, plus the GET path and query-param names (PRD §12.1, marked `# CONFIRM` in
  `inbound.py` / `agritrack.py`). The candidate is built tolerantly behind the port; rename and bump
  the version when confirmed - no consumer breaks until then.
- Apply the receiver-tolerance rule ([[feedback_receiver_tolerance_wire_fields]]): treat unknown or
  empty as absent. (done - `extra="ignore"` + nullable throughout.)
