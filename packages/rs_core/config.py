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


class ArrivalSource(StrEnum):
    # ⚑ CONFIRM: parked decision. Default is DB polling, behind an interface so
    # webhook / LISTEN-NOTIFY can swap in without touching ingestion.
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

    # Gateway push (⚑ CONFIRM: provided by gateway team)
    gateway_push_url: str = ""
    gateway_auth_token: str = ""

    # Arrival notification (⚑ CONFIRM)
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
