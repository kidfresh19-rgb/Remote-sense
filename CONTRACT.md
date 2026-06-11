# External contract (frozen boundary)

This file is the human-readable record of the remote-sense external boundary. The machine-checkable
snapshot is `contract/openapi.before.json` (regenerated only by deliberate, agreed change via
`contract/snapshot_openapi.py`); the enforcing tests live in `tests/contract/`. Rationale and the
rebuild plan: `docs/adr/0008-ordered-reimplementation-frozen-contract.md`,
`docs/prd/0001-intelligence-core-rebuild.md`, `docs/plan/S0.1-S0.2-contract-safety-net.md`.

## The rule

A change to an **EXTERNAL-FROZEN** route or to the outbound push payload is a **regression** if it is
non-additive, that is if it:

- removes or renames a path or operation;
- removes or renames a field, or makes an optional field required;
- changes a field type, or a response status code;
- changes or removes a required auth header.

Additive changes are allowed: a new route, a new optional request field, a new response field. The
diff gate fails only on the non-additive set, and only for frozen routes. The browser BFF is exempt.

Changing the external contract would force the gateway / AgriTrack team to adapt, which is explicitly
out of scope for the rebuild. Freeze first, rebuild behind it.

## Route classification

Resolved from the live OpenAPI snapshot (21 paths) and the router sources.

### EXTERNAL-FROZEN (served, gateway / AgriTrack-facing)

| Method + path | Purpose | Auth | Request -> Response |
|---|---|---|---|
| `POST /api/v1/mobile/sync` | AgriTrack inbound farm/field/sub-plot onboarding (`integrations.py`) | `X-Api-Key` | AgriTrack farm shape (`_FarmSyncIn` family) -> `FarmIngestReport` |
| `GET /api/v1/mobile/data` | AgriTrack results pull | `X-Api-Key` | (query) -> `list[SatelliteResult]` |
| `POST /ingest/farm` | Generic vendor-neutral ingestion (`ingestion.py`) | none today (DI-1 open) | `FarmIn` -> `FarmIngestReport` |

> **FROZEN-CANDIDATE: `POST /ingest/farm`.** Confirm whether the live gateway calls `/ingest/farm`
> or only `/api/v1/mobile/sync`. If only the latter, `/ingest/farm` is internal and can leave the
> frozen set. Tagged frozen for now so the rebuild cannot silently break it. (Owner / gateway-team
> fact; taken as frozen by default while the owner is away.)

### EXTERNAL-FROZEN (outbound wire format, emitted by us, not a served route)

| Emitter | Destination | Auth | Payload |
|---|---|---|---|
| `rs_sync.build_payload` / `to_satellite_results` | `POST {RS_AGRITRACK_BASE_URL}/integrations/satellite/results` | `X-Api-Key` | `GatewayPayload`: `SatelliteResult[]` + `PublishedNarrative[]` (geometry-free, additive, idempotent) |
| `rs_sync` HTTP gateway adapter | `RS_GATEWAY_PUSH_URL` | `Authorization: Bearer <RS_GATEWAY_AUTH_TOKEN>` | same `GatewayPayload` |

This shape does not appear in the served OpenAPI, so it is pinned by `tests/contract/test_outbound_payload.py`.

### INTERNAL-IMPROVABLE (browser BFF, `workspace.py`, we own both ends)

`GET /farms` · `POST /farms/{canonical_farm_id}/publish` · `GET /farms/{canonical_farm_id}/publish/status` ·
`GET /farms/{canonical_farm_id}/fields` · `GET /fields/{field_id}/timeseries` · `GET /fields/{field_id}/scenes` ·
`GET /fields/{field_id}/interpretations` · `PATCH /fields/{field_id}/interpretations/{interpretation_id}` ·
`GET /interpretations/review-queue` · `GET /fields/{field_id}/audit` · `POST /fields/{field_id}/collect` ·
`GET /fields/{field_id}/annotations` · `POST /fields/{field_id}/annotations` ·
`DELETE /fields/{field_id}/annotations/{annotation_id}` · `POST /analyse/aoi`

Auth: `Authorization: Bearer <JWT>` (RBAC). May change freely as long as the frontend is kept in step.

### INTERNAL-OPS / INFRA (not external)

`GET /pipeline/health` (`operations.py`), `GET /healthz`, `GET /readyz`. Not frozen. The former
`POST /publish/farm/{id}` duplicate publish trigger was folded into the workspace's
`POST /farms/{id}/publish` (S2.1 consolidation, 2026-06-11).

## Auth headers

| Header | Where | Env var | Frozen? |
|---|---|---|---|
| `X-Api-Key` | AgriTrack inbound (`/api/v1/mobile/*`) and our outbound results push | `RS_AGRITRACK_API_KEY` | yes |
| `Authorization: Bearer <token>` | outbound to `RS_GATEWAY_PUSH_URL` (http gateway adapter) | `RS_GATEWAY_AUTH_TOKEN` | yes |
| `Authorization: Bearer <JWT>` | browser BFF (RBAC) | `RS_JWT_SECRET` / `RS_JWT_ALGORITHM` (HS256) / `RS_JWT_AUDIENCE` | internal |

## Environment contract (no new credentials)

All config is `RS_`-prefixed (`rs_core/config.py`). The rebuild reuses these names exactly. It must
not invent, rename, or duplicate any credential variable. `.env.example` is the contract; keep it
complete with dummy values.

- App / obs: `RS_APP_ENV`, `RS_LOG_LEVEL`, `RS_OTEL_ENABLED`, `RS_OTEL_SERVICE_NAME`, `RS_OTEL_EXPORTER_OTLP_ENDPOINT`
- Data stores: `RS_DATABASE_URL`, `RS_REDIS_URL`, `RS_MINIO_ENDPOINT`, `RS_MINIO_ACCESS_KEY`, `RS_MINIO_SECRET_KEY`, `RS_MINIO_BUCKET`, `RS_MINIO_SECURE`
- Imagery: `RS_IMAGERY_ADAPTER`, `RS_BACKFILL_MONTHS`
- CDSE: `RS_CDSE_TOKEN_URL`, `RS_CDSE_CLIENT_ID`, `RS_CDSE_CLIENT_SECRET`, `RS_CDSE_STAC_URL`, `RS_CDSE_STAC_COLLECTION`, `RS_CDSE_S3_ENDPOINT`, `RS_CDSE_S3_ACCESS_KEY`, `RS_CDSE_S3_SECRET_KEY`, `RS_CDSE_S3_REGION`, `RS_CDSE_PROCESS_URL`
- Weather / activity: `RS_WEATHER_ADAPTER`, `RS_WEATHER_API_URL`, `RS_ACTIVITY_ADAPTER`, `RS_ACTIVITY_API_URL`
- Gateway / AgriTrack: `RS_GATEWAY_ADAPTER`, `RS_GATEWAY_PUSH_URL`, `RS_GATEWAY_AUTH_TOKEN`, `RS_GATEWAY_MAX_CONCURRENCY`, `RS_AGRITRACK_BASE_URL`, `RS_AGRITRACK_API_KEY`
- Arrival / interpretation / auth / cors: `RS_ARRIVAL_SOURCE`, `RS_ANTHROPIC_API_KEY`, `RS_ANTHROPIC_MODEL`, `RS_JWT_SECRET`, `RS_JWT_ALGORITHM`, `RS_JWT_AUDIENCE`, `RS_CORS_ALLOW_ORIGINS`

Credential variables that must never be renamed or duplicated: `RS_AGRITRACK_API_KEY`,
`RS_GATEWAY_AUTH_TOKEN`, `RS_CDSE_CLIENT_ID`, `RS_CDSE_CLIENT_SECRET`, `RS_CDSE_S3_ACCESS_KEY`,
`RS_CDSE_S3_SECRET_KEY`, `RS_MINIO_ACCESS_KEY`, `RS_MINIO_SECRET_KEY`, `RS_JWT_SECRET`,
`RS_ANTHROPIC_API_KEY`, and the credentials embedded in `RS_DATABASE_URL`.

## How the gate works

1. `contract/snapshot_openapi.py` writes `contract/openapi.before.json` (deterministic, sorted).
2. `tests/contract/test_openapi_diff.py` regenerates the live schema and fails on any non-additive
   change to a frozen path.
3. `tests/contract/test_frozen_routes.py` asserts the frozen routes exist with their method, status,
   required fields, and `X-Api-Key` requirement.
4. `tests/contract/test_outbound_payload.py` pins the outbound `GatewayPayload` shape.
5. CI runs all of the above on every push and PR.
