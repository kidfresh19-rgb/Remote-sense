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
from rs_core.db import get_read_session, get_session
from rs_core.logging import get_logger
from rs_core.repositories import published_narratives_for_farm
from rs_core.schemas import FarmIn, FarmIngestReport, FieldIn
from rs_sync import SatelliteResult, build_payload, to_satellite_results
from rs_sync.payload import PublishedNarrative
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.ingestion import IngestionError, ingest_farm
from services.worker.publish_utils import fetch_farm_results_with_farm_averages

log = get_logger("integrations.agritrack")

router = APIRouter(prefix="/api/v1/mobile", tags=["agritrack"])


# -- AgriTrack inbound payload (their shape; see ADR 0006). extra="ignore" keeps us tolerant of the
# id mirrors and any extra fields the app sends. -------------------------------------------------


class _SubPlotIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    plot_id: str | int
    name: str | None = None
    crop: str | None = None
    boundary: dict[str, Any] | None = None


class _FieldSyncIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    field_id: str | int
    name: str | None = None
    crop: str | None = None
    boundary: dict[str, Any] | None = None
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
    farmer_id = (
        str(sync.farmer.agritrack_id)
        if sync.farmer and sync.farmer.agritrack_id is not None
        else None
    )
    farms: list[FarmIn] = []
    for farm in sync.farms:
        fields: list[FieldIn] = []
        for f in farm.fields:
            field_key = str(f.field_id)
            if f.boundary is not None:
                fields.append(
                    FieldIn(
                        canonical_field_id=field_key,
                        name=f.name,
                        crop=f.crop,
                        geometry=f.boundary,
                    )
                )
            for plot in f.sub_plots:
                if plot.boundary is not None:
                    plot_id_str = str(plot.plot_id)
                    if plot_id_str.startswith(f"{field_key}."):
                        subplot_key = plot_id_str
                    else:
                        subplot_key = f"{field_key}.{plot_id_str}"
                    fields.append(
                        FieldIn(
                            canonical_field_id=subplot_key,
                            name=plot.name,
                            crop=plot.crop,
                            geometry=plot.boundary,
                        )
                    )
        farms.append(
            FarmIn(
                canonical_farm_id=str(farm.farm_id),
                agritrack_farmer_id=farmer_id,
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


async def farm_satellite_results(
    session: AsyncSession, canonical_farm_id: str
) -> list[SatelliteResult]:
    """A farm's stored analyses as AgriTrack /results records, shaped by the same
    `to_satellite_results` the outbound push uses (so push and pull never diverge), with each
    field/pass's published agronomic narrative attached (only published reads, risk #6). Selects
    canonical ids + stats + published narratives only; geometry is never read (invariant 6). Raises
    ValueError if the farm id is not an AgriTrack integer id."""
    results = await fetch_farm_results_with_farm_averages(session, canonical_farm_id)
    if not results:
        return []
    narratives = [
        PublishedNarrative(canonical_field_id=cfid, pass_date=pass_date, narrative=narrative)
        for cfid, pass_date, narrative in await published_narratives_for_farm(
            session, canonical_farm_id
        )
    ]
    return to_satellite_results(build_payload(canonical_farm_id, results, narratives=narratives))


# Rides the read engine (S4.4): results are pushed additively, so replica lag only delays how
# soon a brand-new result appears in a recovery pull, never its correctness. The docstring is
# the frozen route's OpenAPI description and must stay byte-identical (tests/contract).
@router.get("/data", response_model=list[SatelliteResult])
async def mobile_data(
    farm_id: str,
    session: AsyncSession = Depends(get_read_session),
    _: None = Depends(require_agritrack_key),
) -> list[SatelliteResult]:
    """Pull a farm's stored satellite results in the AgriTrack contract shape (ADR 0006 section 4);
    AgriTrack triggers this per farm when a push could not be delivered. Geometry is never returned
    (invariant 6)."""
    try:
        return await farm_satellite_results(session, farm_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
