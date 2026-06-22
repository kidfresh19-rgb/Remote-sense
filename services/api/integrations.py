"""AgriTrack integration edge (ADR 0006). The inbound sync endpoint `POST /api/v1/mobile/sync`
receives a farmer's farms/fields/sub-plots from AgriTrack and maps them onto the vendor-neutral
ingestion schema (`FarmIn`), so all AgriTrack-specific shape lives here and `ingest_farm` stays
generic (invariant 1). Sub-plots are flattened into fields and the AgriTrack ids are encoded in the
canonical join keys (farm "2", field "4", sub-plot "4.1"); the outbound push decodes them back to
integers. Authenticated with the shared AgriTrack API key (the same key we present as
`X-Api-Key`)."""

from __future__ import annotations

import hmac
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, model_validator
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


class _FlatFieldIn(BaseModel):
    """Gateway §6.2 canonical flat-format field (camelCase keys)."""

    model_config = ConfigDict(extra="ignore")
    fieldId: str | int
    name: str | None = None
    crop: str | None = None
    area_ha: float | None = None
    boundary: dict[str, Any] | None = None


class _FlatSubPlotIn(BaseModel):
    """Gateway §6.2 canonical flat-format sub-plot; fieldId links it to its parent field."""

    model_config = ConfigDict(extra="ignore")
    subPlotId: str | int
    fieldId: str | int
    name: str | None = None
    crop: str | None = None
    boundary: dict[str, Any] | None = None


class AgriTrackSyncIn(BaseModel):
    """One AgriTrack sync push: a farmer and their farms."""

    # Supports two formats permanently:
    # - Legacy nested:   farmer + farms[fields[sub_plots]]
    # - Canonical flat:  farmerId + farmId + farm + boundaries + fields + subPlots  (§6.2)
    # Dispatch in to_farm_ins is keyed on whether farmId is present.
    #
    # `farms` stays required so the frozen OpenAPI schema is unchanged; the mode="before"
    # validator synthesizes farms=[] when the flat format sends farmId instead (additive).
    model_config = ConfigDict(extra="ignore")
    # Legacy nested format
    farmer: _FarmerSyncIn | None = None
    farms: list[_FarmSyncIn]
    # Canonical flat format (gateway §6.2, camelCase additive fields)
    farmerId: str | int | None = None
    farmId: str | int | None = None
    farm: dict[str, Any] | None = None
    boundaries: dict[str, Any] | None = None
    fields: list[_FlatFieldIn] = []
    subPlots: list[_FlatSubPlotIn] = []

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_format(cls, data: object) -> object:
        """When the caller uses the flat format (farmId present, farms absent), synthesize an
        empty farms list so the required-field constraint is satisfied without altering the
        frozen OpenAPI schema. to_farm_ins dispatches on farmId to handle the flat path."""
        if isinstance(data, dict) and "farmId" in data and "farms" not in data:
            data = dict(data)
            data["farms"] = []
        return data


def to_farm_ins(sync: AgriTrackSyncIn) -> list[FarmIn]:
    """Map an AgriTrack sync payload to vendor-neutral FarmIn list. Dispatches on format: the
    canonical flat format (§6.2, `farmId` present) calls `_flat_to_farm_ins`; the legacy nested
    format calls `_nested_to_farm_ins`. Both flatten sub-plots into `FieldIn` entries with dotted
    canonical ids (`"{field_id}.{sub_plot_id}"`), so field and sub-plot scope are both analysed
    and results can be reported at both levels (ADR 0006)."""
    if sync.farmId is not None:
        return _flat_to_farm_ins(sync)
    return _nested_to_farm_ins(sync)


def _nested_to_farm_ins(sync: AgriTrackSyncIn) -> list[FarmIn]:
    """Handle the legacy nested format (farmer + farms[fields[sub_plots]]). AgriTrack sends
    GeoJSON in EPSG:4326 (the FieldIn/FarmIn default)."""
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


def _flat_to_farm_ins(sync: AgriTrackSyncIn) -> list[FarmIn]:
    """Handle the canonical flat format (§6.2): a single farm sent as `farmId` + top-level
    `fields` + top-level `subPlots`. Sub-plots carry a `fieldId` back-reference that links each
    sub-plot to its parent field without nesting."""
    farmer_id = str(sync.farmerId) if sync.farmerId is not None else None
    farm_id_str = str(sync.farmId)
    farm_meta = sync.farm or {}
    farm_boundary = (sync.boundaries or {}).get("farm")

    # Index sub-plots by their parent fieldId for O(1) lookup per field.
    sub_plots_by_field: defaultdict[str, list[_FlatSubPlotIn]] = defaultdict(list)
    for sp in sync.subPlots:
        sub_plots_by_field[str(sp.fieldId)].append(sp)

    fields: list[FieldIn] = []
    for f in sync.fields:
        field_key = str(f.fieldId)
        if f.boundary is not None:
            fields.append(
                FieldIn(
                    canonical_field_id=field_key,
                    name=f.name,
                    crop=f.crop,
                    geometry=f.boundary,
                )
            )
        for sp in sub_plots_by_field[field_key]:
            if sp.boundary is not None:
                subplot_key = f"{field_key}.{sp.subPlotId}"
                fields.append(
                    FieldIn(
                        canonical_field_id=subplot_key,
                        name=sp.name,
                        crop=sp.crop,
                        geometry=sp.boundary,
                    )
                )

    return [
        FarmIn(
            canonical_farm_id=farm_id_str,
            agritrack_farmer_id=farmer_id,
            name=farm_meta.get("name"),
            region=farm_meta.get("location"),
            boundary=farm_boundary,
            fields=fields,
        )
    ]


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
