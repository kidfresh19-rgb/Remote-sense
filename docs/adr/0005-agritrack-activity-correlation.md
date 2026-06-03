# ADR 0005 — AgriTrack activity correlation (read-only, behind a port)

- Status: accepted
- Date: 2026-06-03
- Phase: Tier 2 of the improvement plan (the differentiator)
- Builds on: ADR 0001 (ports and adapters), ADR 0004 (weather port)

## Context

remote-sense is the analysis backbone behind AgriTrack, the farmer app. AgriTrack records what
happened on the ground (planting, fertiliser, irrigation, spraying, harvest, scouting). Correlating
those interventions against the satellite signal is the differentiator neither Copernicus Browser
nor EOSDA has: they see the vegetation, not the field-activity log. The constraint is split
ownership (CLAUDE.md invariant 6): AgriTrack owns this data; remote-sense must treat it as
read-only and join on the canonical farm/field id, never write it.

## Decision

A new `rs_activity` package, ports-and-adapters like imagery and weather:

- `ActivityLogPort` — read-only `logs_for_field`. The `mock` adapter gives deterministic synthetic
  logs for offline testing; the real `gateway` adapter (the AgriTrack feed) is parked on the same
  `⚑ CONFIRM` contract as the gateway push and raises until confirmed. Active adapter is a config
  switch (`RS_ACTIVITY_ADAPTER`).
- `correlate(activities, dates, ndvi)` — pure: for each activity it finds the baseline NDVI on or
  before the activity and the mean NDVI over the passes within a response window after it, and the
  delta. It takes the same primitive date/value series the alerts and phenology layers use, so
  `rs_activity` stays independent of `rs_core`/`rs_analysis` types.

Activity logs are **not stored** in remote-sense: they are fetched read-only through the port and
correlated on demand. That keeps invariant 6 intact (no field-activity data is owned or written
here) and adds no migration. The workspace overlay endpoint (fetch logs + correlate against the
field's stored NDVI series) is the thin wiring follow-on.

## Consequences

- The workspace can overlay activities on the index timeline and annotate each with the vegetation
  response that followed (e.g. "fertiliser on 12 Jan, NDVI +0.12 over the next 30 days").
- Because correlation is on-demand and read-only, AgriTrack remains the single owner of the logs;
  remote-sense never diverges from it.
- The real feed is one adapter behind the port, confirmed with the gateway team alongside the push
  contract; nothing downstream changes when it lands.
