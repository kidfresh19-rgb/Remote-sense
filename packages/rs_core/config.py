"""Environment-driven configuration. The active imagery adapter and gateway target are
config switches; no endpoint or secret is ever hard-coded (CLAUDE.md invariant 8)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ImageryAdapter(StrEnum):
    MOCK = "mock"
    SERVER_COMPUTE = "server_compute"
    WINDOWED_COG = "windowed_cog"


class WeatherAdapter(StrEnum):
    MOCK = "mock"
    OPEN_METEO = "open_meteo"


class ActivityAdapter(StrEnum):
    MOCK = "mock"
    GATEWAY = "gateway"


class GatewayAdapter(StrEnum):
    """Outbound results push target. Confirmed 2026-06-04 (ADR 0006): the AgriTrack contract."""

    RECORDING = "recording"
    HTTP = "http"
    AGRITRACK = "agritrack"


class ArrivalSource(StrEnum):
    # Confirmed 2026-06-03: DB polling is the locked default. The interface stays so webhook /
    # LISTEN-NOTIFY can swap in later without touching ingestion, but no longer a parked decision.
    DB_POLL = "db_poll"
    WEBHOOK = "webhook"
    LISTEN_NOTIFY = "listen_notify"


class Settings(BaseSettings):
    # env_ignore_empty: .env.example documents "empty = use the default" (e.g.
    # RS_COG_RETENTION_MONTHS=, RS_CDSE_RATE_LIMIT_RPS=); without it an empty value crashes the
    # numeric-optional fields at boot instead of falling back.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RS_",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # App
    app_env: str = "dev"
    log_level: str = "INFO"

    # Observability (OpenTelemetry). Tracing is opt-in; when disabled, or when the OTel
    # packages / an OTLP endpoint are absent, the stack boots identically with tracing as a
    # safe no-op. Logs carry the active trace/span ids so logs and traces cross-reference.
    otel_enabled: bool = False
    otel_service_name: str = "remote-sense"
    otel_exporter_otlp_endpoint: str = ""  # e.g. http://otel-collector:4318

    # PostgreSQL + PostGIS
    database_url: str = "postgresql+psycopg://rs:rs@localhost:5432/remote_sense"
    # Connection pooling (S4.4). Defaults match SQLAlchemy's own; tune per deployment via env.
    # pre-ping is always on (rs_core.db), so recycled/dead connections never reach a request.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_s: float = 30.0
    db_pool_recycle_s: int = 1800
    # ⚑ CONFIRM (S4.4): a streaming-replica DSN for analytical reads. Empty = reads stay on the
    # primary (the only mode until a replica is provisioned). When set, the read-routed
    # endpoints (field/farm reads, the mobile data pull) may lag the primary by the replication
    # delay; review-workflow and annotation reads stay on the primary for read-after-write.
    database_read_url: str = ""

    # Redis (cache, Celery broker, quota counters)
    redis_url: str = "redis://localhost:6379/0"

    # Object storage (MinIO / S3)
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "remote-sense"
    minio_secure: bool = False

    # Imagery access layer
    imagery_adapter: ImageryAdapter = ImageryAdapter.MOCK
    backfill_months: int = 18
    # CONFIRMED 2026-06-13 (S4.3): COG retention horizon. None = match backfill_months, so
    # index-preview COGs exist exactly for the history depth the workspace advertises; older
    # passes keep their stats/provenance rows but lose the raster overlay.
    cog_retention_months: int | None = None

    # CDSE (endpoint deliberately unspecified in code; provided via env)
    # Quota confirmed 2026-06-12 (S4.5 resolved): a CDSE general account allows 300 Process-API
    # requests/min, the binding limit for the one budget shared by STAC search, Process API
    # renders, and windowed/metadata reads across all workers. Production runs 4 rps (80% of
    # quota; with burst 10 no 60s window can exceed 250). None = governance off (reactive 429
    # backoff still applies) - the default stays None because the value belongs to the deployed
    # account, so .env carries it, not code.
    cdse_rate_limit_rps: float | None = None
    cdse_rate_limit_burst: float = 10.0
    cdse_token_url: str = ""
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    cdse_stac_url: str = ""
    # windowed_cog adapter: the STAC collection to search and the eodata S3 store the product
    # rasters live in (reached over GDAL /vsis3/). S3 keys are issued from the CDSE dashboard and
    # are distinct from the OAuth2 client credentials above.
    cdse_stac_collection: str = "sentinel-2-l2a"  # CDSE STAC v1 L2A collection id
    cdse_s3_endpoint: str = ""  # e.g. eodata.dataspace.copernicus.eu
    cdse_s3_access_key: str = ""
    cdse_s3_secret_key: str = ""
    cdse_s3_region: str = "default"
    # server_compute adapter (ADR 0003): the CDSE Process API endpoint that renders index previews
    # and returns reflectance bands server-side. e.g. https://sh.dataspace.copernicus.eu/api/v1/process
    cdse_process_url: str = ""

    # Weather access layer (improvement plan Tier 1, ADR 0004). Active adapter is a config switch.
    weather_adapter: WeatherAdapter = WeatherAdapter.MOCK
    weather_api_url: str = ""  # real provider base URL (e.g. Open-Meteo); empty for mock

    # AgriTrack activity logs (Tier 2, ADR 0005). Read-only; the active adapter is a config switch.
    activity_adapter: ActivityAdapter = ActivityAdapter.MOCK
    activity_api_url: str = ""  # real AgriTrack/gateway feed base URL; empty for mock

    # Gateway push. Confirmed 2026-06-04 (ADR 0006): the AgriTrack contract.
    gateway_adapter: GatewayAdapter = GatewayAdapter.RECORDING
    gateway_push_url: str = ""
    gateway_auth_token: str = ""
    gateway_max_concurrency: int = 10
    # AgriTrack integration (ADR 0006). One key both ways: presented as X-Api-Key to their
    # /integrations/satellite/results, and required on inbound /api/v1/mobile/* calls.
    agritrack_base_url: str = ""
    agritrack_api_key: str = ""
    # ⚑ CONFIRM (S4.6 / DI-1): enforce the same shared X-Api-Key on POST /ingest/farm. The route
    # is EXTERNAL-FROZEN (confirmed 2026-06-12: the gateway still calls it), so enforcement
    # ships OFF: keyless calls work unchanged and only log. Flip to true once the gateway team
    # confirms they send the key on ingest; ingest.keyless_call / ingest.key_mismatch logs
    # must have gone quiet first.
    ingest_require_key: bool = False

    # Arrival notification (confirmed 2026-06-03: DB polling; webhook/LISTEN-NOTIFY swap in later).
    arrival_source: ArrivalSource = ArrivalSource.DB_POLL

    # Interpretation layer (Claude API)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Auth / RBAC (Phase 7). The IdP/gateway issues JWTs; the API only verifies them (HS256).
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_audience: str = ""

    # CORS. The analyst workspace (L6) is a browser SPA on a separate origin, so the API must allow
    # that origin for cross-origin requests and their preflight. Comma-separated; dev default is the
    # Vite server. Set the deployed workspace origin(s) in production; empty disables CORS.
    cors_allow_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
