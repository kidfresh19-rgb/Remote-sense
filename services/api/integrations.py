"""AgriTrack integration edge (ADR 0006). The inbound sync endpoint `POST /api/v1/mobile/sync`
receives a farmer's farms/fields/sub-plots from AgriTrack and maps them onto the vendor-neutral
ingestion schema (`FarmIn`), so all AgriTrack-specific shape lives here and `ingest_farm` stays
generic (invariant 1). Sub-plots are flattened into fields and the AgriTrack ids are encoded in the
canonical join keys (farm "2", field "4", sub-plot "4.1"); the outbound push decodes them back to
integers. Authenticated with the shared AgriTrack API key (the same key we present as
`X-Api-Key`)."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from rs_core.config import Settings, get_settings
from rs_core.db import get_session
from rs_core.logging import get_logger
from rs_core.schemas import FarmIn, FarmIngestReport, FieldIn
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.ingestion import IngestionError, ingest_farm

log = get_logger("integrations.agritrack")

router = APIRouter(prefix="/api/v1/mobile", tags=["agritrack"])


# -- AgriTrack inbound payload (their shape; see ADR 0006). extra="ignore" keeps us tolerant of the
# id mirrors and any extra fields the app sends. -------------------------------------------------


class _SubPlotIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    plot_id: str | int
    name: str | None = None
    crop: str | None = None
    boundary: dict[str, Any]


class _FieldSyncIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    field_id: str | int
    name: str | None = None
    crop: str | None = None
    boundary: dict[str, Any]
    sub_plots: list[_SubPlotIn] = []


class _FarmSyncIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    farm_id: str | int
    name: str | None = None
    location: str | None = None
    boundary: dict[str, Any] | None = None
    fields: list[_FieldSyncIn] = []


class _FarmerSyncIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    agritrack_id: str | int | None = None
    name: str | None = None
    email: str | None = None


class AgriTrackSyncIn(BaseModel):
    """One AgriTrack sync push: a farmer and their farms."""

    model_config = ConfigDict(extra="ignore")
    farmer: _FarmerSyncIn | None = None
    farms: list[_FarmSyncIn]


def to_farm_ins(sync: AgriTrackSyncIn) -> list[FarmIn]:
    """Map an AgriTrack sync payload onto the vendor-neutral ingestion schema. Each field becomes a
    `FieldIn`; each sub-plot becomes another `FieldIn` whose canonical id encodes its parent field
    (`"{field_id}.{plot_id}"`), so both are analysed and results can be reported at field and
    sub-plot scope (ADR 0006). AgriTrack sends GeoJSON in EPSG:4326 (the FieldIn/FarmIn
    default)."""
    farms: list[FarmIn] = []
    for farm in sync.farms:
        fields: list[FieldIn] = []
        for f in farm.fields:
            field_key = str(f.field_id)
            fields.append(
                FieldIn(canonical_field_id=field_key, name=f.name, crop=f.crop, geometry=f.boundary)
            )
            for plot in f.sub_plots:
                fields.append(
                    FieldIn(
                        canonical_field_id=f"{field_key}.{plot.plot_id}",
                        name=plot.name,
                        crop=plot.crop,
                        geometry=plot.boundary,
                    )
                )
        farms.append(
            FarmIn(
                canonical_farm_id=str(farm.farm_id),
                name=farm.name,
                region=farm.location,
                boundary=farm.boundary,
                fields=fields,
            )
        )
    return farms


async def require_agritrack_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate the inbound AgriTrack endpoints on the shared API key (ADR 0006), compared in constant
    time. The same key we present to AgriTrack as `X-Api-Key` authenticates their calls to us."""
    if not settings.agritrack_api_key:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "AgriTrack API key is not configured"
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.agritrack_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-Api-Key")


class SyncResponse(BaseModel):
    """Per-sync outcome: how many farms were ingested and each farm's ingestion report."""

    synced: int
    farms: list[FarmIngestReport]


@router.post("/sync", response_model=SyncResponse, status_code=status.HTTP_200_OK)
async def mobile_sync(
    payload: AgriTrackSyncIn,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_agritrack_key),
) -> SyncResponse:
    """Ingest an AgriTrack sync (farmer -> farms -> fields -> sub-plots). Idempotent: re-sending the
    same payload changes nothing (the ingestion layer dedupes on the canonical ids). Bad geometry is
    rejected with 422."""
    reports: list[FarmIngestReport] = []
    try:
        for farm_in in to_farm_ins(payload):
            reports.append(await ingest_farm(session, farm_in))
    except IngestionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    log.info("agritrack.sync", farms=len(reports))
    return SyncResponse(synced=len(reports), farms=reports)
