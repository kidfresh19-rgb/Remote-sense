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
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="RS_", extra="ignore", case_sensitive=False
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

    # CDSE (endpoint deliberately unspecified in code; provided via env)
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
