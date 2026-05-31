# ADR 0001 — Ports & adapters for the imagery access layer

- Status: accepted
- Date: 2026-05-31
- Phase: 0 (Foundations)

## Context

remote-sense must read Sentinel-2 imagery from an external source (CDSE), but we want to avoid
committing to any single endpoint, and we want the platform to be fully testable without network
access or credentials. The source may be reached two ways: an endpoint that computes indices
server-side (fast previews, live tiles) and direct windowed reads of Cloud-Optimised GeoTIFFs
(the stored backfill pipeline, where we must own the reflectance math for correctness).

## Decision

All satellite access goes through a single port, `rs_imagery.AccessPort`, with four operations:
`search`, `metadata`, `fetch`, `preview`. Endpoint specifics live only in adapters:

- `mock` — deterministic synthetic data; the whole platform tests against the contract.
- `server_compute` — endpoint-side index computation (Phase 4).
- `windowed_cog` — windowed COG reads, engine owns the math (Phase 2/3).

Every adapter returns the same normalized shape: reflectance-corrected data, a known CRS, a
clear-pixel fraction, and a provenance tag. The active adapter is selected by `RS_IMAGERY_ADAPTER`
config, so the source choice is reversible and never wired into downstream code. Resilience
(OAuth2 tokens, retry/backoff, circuit-breaking, quota) is centralized in adapters.

## Consequences

- Phases 2/3/4 build and test against `mock` before any real adapter exists, decoupling the
  schedule from CDSE access and quota.
- Adding a second provider later is a new adapter with zero downstream change.
- The two real adapters must agree on values for the same scene + formula; parity is asserted in
  the validation matrix (QA).
- The same pattern governs the gateway push (`rs_sync.GatewayPort`), keeping both external edges
  symmetric and testable.
