"""Ingestion service (L1): turn a gateway farm payload into validated, immutably-versioned
farm/field rows. This is the only writer of farm + field geometry on the remote-sense side
(CLAUDE.md invariant 6 - no field is written by both systems).

What it enforces:
  * geometry validity + normalisation to WGS84 (geo.validate_geometry);
  * CRS → working UTM by centroid (DI-2);
  * field-within-farm nesting to a survey tolerance (DI-3);
  * a farm with no fields gets one field derived from its boundary (DI-4);
  * idempotent upsert: re-sending an identical payload changes nothing;
  * geometry versioning: a genuinely changed boundary bumps the version, archives the prior
    boundary, and flags the field for fresh backfill while retaining old analyses (DI-5).

One submodule per layer: validation (pure geometry checks, no DB), persistence (get-or-create
plus the versioned upsert), service (the orchestrator and the EXTERNAL-FROZEN POST /ingest/farm
entrypoint). Every consumed name is re-exported here so callers keep importing from
services.api.ingestion."""

from services.api.ingestion.persistence import (
    _match_existing,
    get_or_create_farm,
    get_or_create_field,
)
from services.api.ingestion.service import (
    _enqueue_field_backfills,
    ingest_farm,
    ingest_farm_endpoint,
    router,
)
from services.api.ingestion.validation import (
    IngestionError,
    _as_multipolygon,
    _check_nesting,
    _resolve_farm_boundary,
    _validate_fields,
)

__all__ = [
    # The underscore names are part of the tested surface: the no-DB decision-logic suite and
    # the DB suite import them directly (tests/test_ingestion_logic.py, test_ingestion_db.py).
    "IngestionError",
    "_as_multipolygon",
    "_check_nesting",
    "_enqueue_field_backfills",
    "_match_existing",
    "_resolve_farm_boundary",
    "_validate_fields",
    "get_or_create_farm",
    "get_or_create_field",
    "ingest_farm",
    "ingest_farm_endpoint",
    "router",
]
