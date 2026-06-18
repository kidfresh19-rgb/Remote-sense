# Natural Region seed layer (provenance)

This directory holds the Zimbabwe Natural Region (agro-ecological zone) boundary layer that
backlog 0002 (PRD 0002 slice 1) seeds read-only at init. It is committed reference geometry
(ADR 0010), not a transient raw band, so it is tracked in git via the `!data/natural_regions/`
exception in `.gitignore`.

## CONFIRM: non-authoritative source (override recorded 2026-06-18)

The current file is **NOT the authoritative ZINGSA AEZ 2020 layer**. The project rulebook
(backlog 0002, `docs/plan/0002-natural-region-foundation-prep.md`) requires the authoritative
Revised Agro-Ecological Zones of Zimbabwe (ZINGSA AEZ 2020, Manatsa et al. 2020, custodian
ZINGSA). That file is still in procurement.

Per an explicit user decision on 2026-06-18, the build was unblocked using a **candidate** file
instead, so that slices 1 and 5 can proceed now. **This file must be replaced by the authoritative
ZINGSA layer when it lands, and the layer re-seeded** (the seed is idempotent on
`(source, year, version)`, so re-seeding with the authoritative version supersedes this one).

## File

| Field | Value |
|-------|-------|
| `file` | `zimbabwe_agroecological_zones_2020_candidate.geojson` |
| `origin` | user-provided candidate (`Agroecological_Zones_of_Zimbabwe_New_*.geojson`), not government-endorsed |
| `crs` | EPSG:4326 (WGS84) |
| `feature_count` | 7 |
| `naming_column` | `gez_name` |
| `zones` | Region I, IIa, IIb, III, IV, Va, Vb (the revised 2020 sub-division of II and V) |
| `bounds (lon/lat)` | [25.237, -22.423, 33.056, -15.609] (Zimbabwe) |
| `geometry_types` | MultiPolygon (6), Polygon (1) |
| `acquisition_date` | 2026-06-18 |

## Layer provenance stamped on the seeded layer

These populate `region_boundary_layer` at seed time (invariant 5, ADR 0010):

- `source`: "candidate (non-authoritative), pending ZINGSA AEZ 2020"
- `year`: 2020
- `version`: "candidate-2026-06-18"
- `publishing_authority`: "UNVERIFIED, replace with Government of Zimbabwe / ZINGSA on authoritative seed"
- `naming_column`: "gez_name"
- `crs`: "EPSG:4326"
- `read_only`: true

When the authoritative file replaces this one, update this table and these values to the ZINGSA
provenance recorded in `docs/plan/0002-natural-region-foundation-prep.md`.
