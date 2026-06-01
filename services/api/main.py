"""FastAPI entrypoint. Phase 0: health + readiness reporting the active configuration.
Phase 1 adds ingestion (L1). The workspace BFF (L6) and the publish trigger (L7) land in
later phases."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from rs_core import (
    configure_logging,
    configure_telemetry,
    get_logger,
    get_settings,
    instrument_fastapi,
    instrument_httpx,
)

from services.api.ingestion import router as ingestion_router
from services.api.operations import router as operations_router
from services.api.workspace import router as workspace_router

settings = get_settings()
log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    tracing = configure_telemetry(settings)
    instrument_httpx()
    log.info(
        "api.startup",
        env=settings.app_env,
        imagery_adapter=settings.imagery_adapter.value,
        tracing=tracing,
    )
    yield
    log.info("api.shutdown")


app = FastAPI(title="remote-sense API", version="0.1.0", lifespan=lifespan)
instrument_fastapi(app)
app.include_router(ingestion_router)
app.include_router(operations_router)
app.include_router(workspace_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, object]:
    return {
        "status": "ready",
        "env": settings.app_env,
        "imagery_adapter": settings.imagery_adapter.value,
        "arrival_source": settings.arrival_source.value,
        "backfill_months": settings.backfill_months,
    }
