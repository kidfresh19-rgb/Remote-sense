# Plan 0002 (prep) - Natural Region foundation: design held ready for the authoritative file

- Status: design-only, no data. The **seed and recompute build remains parked** until the
  authoritative ZINGSA AEZ 2020 file is confirmed in-repo with its metadata.
- Parent: backlog 0002 (`docs/backlog/0002-comparison-groups-natural-region-foundation.md`),
  PRD 0002 slice 1.
- Purpose: capture the no-data-dependency design so the backend-engineer build is one-shot the moment
  the file lands. Nothing here touches real data; the signatures are design, not committed product
  code.

## Hard constraints (carry into the build)

- **Authoritative source only.** The seed file is the Revised Agro-Ecological Zones of Zimbabwe
  (ZINGSA AEZ, 2020), Manatsa et al. (2020), custodian Zimbabwe National Geospatial and Space Agency
  (ZINGSA). Do NOT source a candidate from `zimgeospatial.github.io`, HDX, or any community GitHub
  repo. Their data is not government-endorsed and fails authoritativeness.
- **No placeholder polygons, ever**, not even for testing the seed or recompute. Tests use the
  synthetic fixture below (arbitrary shapes), which is never seed data and never the real zones.
- Two files currently in the user's Downloads (`Agroecological_Zones_of_Zimbabwe_New_*.geojson` /
  `*.geodatabase`) are unverified candidates and MUST NOT be seeded. Procurement of the authoritative
  file is in motion (BUSE / Manatsa primary, ZINGSA, HIT internal); ETA about 1 to 5 days.
- Do not begin seed insertion or the recompute until the authoritative file is confirmed in-repo with
  its metadata, and verified (regions I to V present, valid CRS, the naming column identified).

## Provenance metadata (record on the seeded layer at hand-off)

Layer-level provenance, stored once per seeded layer and version-stamped (ADR 0010, invariant 5).

| Field | Value at hand-off |
|-------|-------------------|
| `source` | "Zimbabwe National Geospatial and Space Agency (ZINGSA)" |
| `year` | 2020 |
| `version` | exact version string from the file (e.g. "ZINGSA AEZ 2020") |
| `publishing_authority` | "Government of Zimbabwe, Ministry of Higher and Tertiary Education, Innovation, Science and Technology Development" |
| `citation` | Manatsa, D., Mushore, T.D., Gwitira, I., Wuta, M., Chemura, A., Shekede, M.D., Mugandani R., Sakala, L.C., Ali, L. H., Masukwedza, G.I., Mupuro, J.M., and Muzira, N.M. (2020). Revision Of Zimbabwe's Agro-Ecological Zones. Government of Zimbabwe under ZINGSA for the Ministry of Higher and Tertiary Education, Innovation, Science and Technology Development. |
| `naming_column` | confirmed at hand-off (likely `NR`, `Region`, `AEZ`, or `ZONE_NAME`) |
| `crs` | source CRS from the `.prj` or GeoPackage metadata; stored as-is, reprojected to UTM for math |
| `acquisition_date` | date the file was received |
| `acquisition_path` | which procurement path succeeded (BUSE/Manatsa, ZINGSA, or HIT) |
| `file_path` | relative path in-repo (proposed `data/natural_regions/`) |

These are distinct from the per-boundary attributes the ADR 0010 amendment added (`source` =
`seeded` | `uploaded` | `drawn`, `creator`, `nr_composition`, `dominant_nr`). For the seeded layer
every boundary carries `source = seeded` and no creator; `nr_composition` is the trivial
`{<self>: 1.0}` and `dominant_nr` is the region itself.

## Loader interface (design, not implementation)

Pure, zero DB and zero network. Reuses the existing geo helpers (`validate_geometry`, `reproject`)
and geopandas (already a `geo` dependency). Raises on a missing CRS rather than guessing.

```python
class ParsedRegionFeature(BaseModel):
    name: str                  # from the name column
    geometry: BaseGeometry     # validated shapely geometry, in the source CRS
    source_crs: str            # e.g. "EPSG:4326"; never inferred

def read_region_layer(path: Path, *, name_column: str) -> list[ParsedRegionFeature]:
    """Read every feature of a .zip shapefile / GeoPackage / GeoJSON via geopandas.
    Validate each geometry; carry the source CRS from the .prj / file metadata.
    Raise RegionLayerError on a missing CRS or an unreadable / empty layer."""
```

## Migration scaffold (column structure)

Three tables via one Alembic migration. Geometry in WGS84 (`geometry(MultiPolygon, 4326)`), reprojected
to UTM 32735/32736 only for area or distance math (invariant CRS). Backend-engineer finalises types.

- `region_boundary_layer` - `id`, `name`, the provenance fields above (`source`, `year`, `version`,
  `publishing_authority`, `citation`, `naming_column`, `crs`, `acquisition_date`, `acquisition_path`,
  `file_path`), `read_only` (true for the seeded layer), `created_at` (UTC). Unique on
  `(source, year, version)` for idempotent re-seed.
- `region_boundary` - `id`, `layer_id` (FK), `name`, `geom`, `source` enum
  (`seeded` | `uploaded` | `drawn`), `creator` (nullable), `nr_composition` (JSONB), `dominant_nr`,
  `created_at`.
- `farm_region_assignment` - `id`, `farm_id` (canonical), `region_boundary_id` (FK), `layer_version`,
  `boundary_adjacent` (bool), `assigned_at` (UTC). Unique on `(farm_id, layer_id)` so a farm has one
  assignment per layer; a re-survey writes a new row stamped with the new `layer_version`.

The seeded layer is read-only: no upload or draw path may edit or overwrite it (acceptance in 0002).

## Recompute hook (design)

```python
@celery_app.task
def recompute_farm_region_assignments(farm_id: str | None = None) -> None:
    """Assign each farm to the region whose polygon contains its centroid, per layer.
    farm_id set: one farm (on register or geometry_version change). None: all farms
    (after a seed or boundary edit). Flag boundary-adjacent when the centroid is within
    the configured edge tolerance, distance computed in the working UTM zone. Stamp the
    layer version. Idempotent: re-running changes nothing."""
```

Trigger events: farm register, farm `geometry_version` change, layer seed, and (later) boundary
upload / draw / edit.

## Open Item 3 - RBAC role mapping (ready for one-shot confirmation)

Proposed mappings, extending the existing view/annotate model in `rs_core` RBAC. Confirm or amend:

- `upload_region_boundary` -> **admin** (bulk multi-feature boundary uploads).
- `create_region_cluster` -> **analyst** (analyst-drawn and single-feature regions).
- `create_cohort` / `manage_cohort` -> **analyst**.
- `view_group` -> **any authenticated user with farm access**.

## Open Item 4 - dominant-NR split threshold (ready for one-shot confirmation)

A region boundary may span Natural Regions. The analysis layer benchmarks within the dominant Natural
Region by default and breaks out a per-Natural-Region view when the composition spread is meaningful.

- Proposed default: treat a boundary as effectively single-Natural-Region when `dominant_nr >= 0.85`
  (so a 95/5 boundary is single-NR; a 50/50 boundary triggers the per-NR split).
- Runtime-configurable via a settings key (not hard-coded). Agronomy-scientist review confirms the
  default before launch (same treatment as the Open Item 1 size buckets).

## Synthetic test fixture (fixture-only, never seed data)

For the pure centroid-containment and boundary-adjacent seam (`test_geo.py` prior art). The polygons
are **synthetic test shapes, not the real zones**, and the labels are arbitrary; this exercises the
algorithm, not the data. Coordinates are GeoJSON `[lon, lat]` in WGS84.

- A partition of five non-overlapping rectangles labelled `NR-A` .. `NR-E` over a synthetic extent
  loosely spanning Zimbabwe's bounding box.
- Test farm centroids placed at real city coordinates purely so the points are spread out, each
  expected to fall in whichever synthetic rectangle geometrically contains it (the expected label is
  derived from the fixture geometry, never asserted from real agronomy):
  - `harare-pt` (31.05, -17.83)
  - `bulawayo-pt` (28.58, -20.15)
  - `mutare-pt` (32.67, -18.97)
  - `bindura-pt` (31.33, -17.30)
  - `edge-pt` placed within the edge tolerance of an A/B border, expected `boundary_adjacent = True`.
- Assertions: deterministic centroid containment (same farm, same region every run), exactly one
  region per layer, the boundary-adjacent flag fires only for `edge-pt`, and the recompute is
  idempotent. The edge tolerance distance is computed after reprojecting to UTM 32735/32736.

## When the file lands (build entry checklist)

1. Confirm the file is in-repo (`data/natural_regions/`) with the metadata table above filled in.
2. Verify: regions I to V present, geometries valid, a defined CRS, the naming column identified.
3. Spawn backend-engineer on backlog 0002 with this design; then backlog 0005.
4. Frontend-engineer takes 0010 / 0011, then 0014.
