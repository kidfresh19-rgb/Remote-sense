# ADR 0004 — WeatherPort: a weather access layer behind a port

- Status: accepted
- Date: 2026-06-03
- Phase: Tier 1 of the improvement plan (match EOSDA's agronomy core)
- Builds on: ADR 0001 (ports and adapters for imagery access)

## Context

EOSDA Crop Monitoring's biggest advantage over a pure index viewer is agronomic decision support
grounded in weather: growing-degree-days, rainfall, and reference evapotranspiration alongside the
satellite signal. remote-sense had none. Weather is a second external data source, so by invariant 1
it must sit behind a port, with provider specifics confined to adapters and the active provider a
config switch, exactly like imagery (ADR 0001) and the gateway push.

## Decision

A new `rs_weather` package with `WeatherPort` (operations `daily` for historical/current weather and
`forecast` for the short range), returning a normalized `WeatherSeries` (daily tmin/tmax/precip plus
provenance). Adapters:

- `mock` — deterministic synthetic weather, no network or credentials, so the whole agronomy stack
  is testable against the contract from day one (the weather analogue of the imagery `MockAdapter`).
- `open_meteo` (next) — a real, free, key-less provider behind the same port, with HTTP behind an
  injected `httpx` client (MockTransport-testable) like the CDSE STAC client.

Agronomy math lives in `rs_weather.agro` as pure functions, reused by every adapter and by the
alerts/interpretation layers:

- **Growing-degree-days** (with a configurable base and an optional upper cap).
- **Reference ET0** via the **FAO-56 Hargreaves** equation, chosen because it needs only tmin/tmax
  (plus latitude and day-of-year for extraterrestrial radiation), so it works from the minimal daily
  series every provider supplies, with no extra inputs (radiation, humidity, wind).
- **Rainfall accumulation**.

Time follows the project rule: weather is keyed by local date; UTC timestamps carry provenance.

## Consequences

- Tier 1 alerts (T1.2) and field-level GDD/ET0 read through this one port; swapping providers later
  is a config change with zero downstream impact.
- The pure agronomy functions are unit-tested with synthetic series and against FAO-56 reference
  behavior, with zero network, satisfying the testing discipline.
- Field-level use (centroid -> series -> accumulated GDD/ET0) is wired where it is consumed
  (alerts and a workspace endpoint), not in the port itself, keeping `rs_weather` infra-free.
