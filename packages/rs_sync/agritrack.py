"""AgriTrack outbound adapter (ADR 0006): the `GatewayPort` that delivers analysis results to
AgriTrack's `POST /integrations/satellite/results`.

It aggregates our per-index results into one record per (field, analysis date) with the contract's
flattened metrics, decodes the canonical ids back to AgriTrack integers (farm "2", field "4",
sub-plot "4.1"), and posts each with the `X-Api-Key` header. All AgriTrack-specific shape (endpoint,
auth, metric names, the classification mapping) lives here (invariant 1), and geometry never leaves
(invariant 6) - the payload carries none."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import httpx
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rs_sync.payload import GatewayPayload, IndexResult
from rs_sync.port import GatewayPort, PushResult

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)
_RESULTS_PATH = "/integrations/satellite/results"

# The NDVI vigour band (rs_interpret) collapses onto the AgriTrack classification enum. Reusing the
# agronomy thresholds (classify) rather than duplicating them; the five vigour bands map to four.
_CLASSIFICATION = {
    "dense": "healthy",
    "vigorous": "healthy",
    "developing": "moderate",
    "sparse": "stressed",
    "bare": "critical",
}
# classification -> the contract's stress_level (their side maps stress_level onto classification).
_STRESS = {"healthy": "none", "moderate": "low", "stressed": "moderate", "critical": "high"}


class SatelliteMetrics(BaseModel):
    """The flattened per-record metrics AgriTrack expects (ADR 0006)."""

    ndvi_mean: float | None = None
    ndvi_min: float | None = None
    ndvi_max: float | None = None
    evi_mean: float | None = None
    ndwi_mean: float | None = None
    cloud_cover_pct: float | None = None
    health_score: float | None = None
    classification: str | None = None


class SatelliteInterpretation(BaseModel):
    """The optional interpretation block: none|low|moderate|high stress plus free-text
    anomalies."""

    stress_level: str | None = None
    anomalies: list[str] = []


class SatelliteResult(BaseModel):
    """One AgriTrack `/integrations/satellite/results` record: a field (or sub-plot) on one analysis
    date. Field names are the contract's camelCase wire keys."""

    sourceSystem: str = "satellite"
    farmId: int
    fieldId: int | None = None
    subPlotId: int | None = None
    scope: str
    analysisDate: str
    extId: str
    metrics: SatelliteMetrics
    interpretation: SatelliteInterpretation | None = None


def _decode_field(canonical_field_id: str | None) -> tuple[int | None, int | None, str]:
    """Decode a canonical field id back to AgriTrack integers + scope: "4" -> (4, None, "field");
    "4.1" -> (4, 1, "sub_plot"); None or unparseable -> (None, None, "farm")."""
    if not canonical_field_id:
        return None, None, "farm"
    head, _, tail = canonical_field_id.partition(".")
    try:
        field_id = int(head)
    except ValueError:
        return None, None, "farm"
    if tail:
        try:
            return field_id, int(tail), "sub_plot"
        except ValueError:
            return field_id, None, "field"
    return field_id, None, "field"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _build_record(
    farm_id: int, canonical_field_id: str | None, pass_date: date, rows: list[IndexResult]
) -> SatelliteResult:
    """Aggregate one (field, date)'s per-index rows into a contract record. NDMI fills `ndwi_mean`
    (ADR 0006); `classification`/`health_score` come from the NDVI vigour band; `cloud_cover_pct`
    is the AOI's non-clear fraction."""
    from rs_interpret import classify  # pure agronomy bands; lazy so rs_sync stays import-light

    by_index = {r.index_name.lower(): r for r in rows}
    ndvi = by_index.get("ndvi")
    evi2 = by_index.get("evi2")
    ndmi = by_index.get("ndmi")
    metrics = SatelliteMetrics(
        ndvi_mean=ndvi.mean if ndvi else None,
        ndvi_min=ndvi.min if ndvi else None,
        ndvi_max=ndvi.max if ndvi else None,
        evi_mean=evi2.mean if evi2 else None,
        ndwi_mean=ndmi.mean if ndmi else None,
        cloud_cover_pct=round((1.0 - rows[0].clear_fraction) * 100.0, 1),
    )
    interpretation: SatelliteInterpretation | None = None
    if ndvi is not None and ndvi.mean is not None:
        classification = _CLASSIFICATION.get(classify("ndvi", ndvi.mean).label)
        metrics.classification = classification
        metrics.health_score = round(_clamp01(ndvi.mean), 2)
        if classification is not None:
            interpretation = SatelliteInterpretation(stress_level=_STRESS.get(classification))

    field_id, sub_plot_id, scope = _decode_field(canonical_field_id)
    return SatelliteResult(
        farmId=farm_id,
        fieldId=field_id,
        subPlotId=sub_plot_id,
        scope=scope,
        analysisDate=pass_date.isoformat(),
        extId=f"{farm_id}:{canonical_field_id or 'farm'}:{pass_date.isoformat()}",
        metrics=metrics,
        interpretation=interpretation,
    )


def to_satellite_results(payload: GatewayPayload) -> list[SatelliteResult]:
    """Aggregate a farm's per-index results into AgriTrack records, one per (field, analysis date).
    Pure and order-stable, so a re-push is byte-identical. Unit-tested with synthetic results."""
    try:
        farm_id = int(payload.canonical_farm_id)
    except ValueError as exc:
        raise ValueError(
            f"AgriTrack farmId must be an integer canonical_farm_id, got "
            f"{payload.canonical_farm_id!r}"
        ) from exc

    groups: dict[tuple[str | None, date], list[IndexResult]] = defaultdict(list)
    for result in payload.results:
        groups[(result.canonical_field_id, result.pass_date)].append(result)

    records = [
        _build_record(farm_id, field_id, pass_date, rows)
        for (field_id, pass_date), rows in groups.items()
    ]
    records.sort(key=lambda s: (s.fieldId or -1, s.subPlotId or -1, s.analysisDate))
    return records


class AgriTrackGatewayPort(GatewayPort):
    """Deliver results to AgriTrack's `/integrations/satellite/results` (ADR 0006): one POST per
    (field, date) record, authenticated with `X-Api-Key`, transient failures retried. `extId`
    deduplicates on AgriTrack's side; the `SyncOutbox` dedupes the whole payload on ours (R-2), so a
    retried push that re-sends already-delivered records is safe."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        max_attempts: int = 4,
        backoff: float = 0.5,
    ) -> None:
        if not base_url:
            raise ValueError("AgriTrackGatewayPort needs RS_AGRITRACK_BASE_URL")
        if not api_key:
            raise ValueError("AgriTrackGatewayPort needs RS_AGRITRACK_API_KEY")
        self._url = base_url.rstrip("/") + _RESULTS_PATH
        self._api_key = api_key
        self._client = client
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff = backoff

    async def push(self, payload: GatewayPayload) -> PushResult:
        records = to_satellite_results(payload)
        if not records:
            return PushResult(ok=True, status="empty")
        try:
            for record in records:
                await self._post(record)
        except _RETRYABLE as exc:
            return PushResult(ok=False, status="error", detail=str(exc))
        return PushResult(ok=True, status="ok", detail=f"{len(records)} records")

    async def _post(self, record: SatelliteResult) -> None:
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json"}
        body = record.model_dump(mode="json")
        async for attempt in AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._backoff, max=10),
            retry=retry_if_exception_type(_RETRYABLE),
        ):
            with attempt:
                if self._client is not None:
                    response = await self._client.post(
                        self._url, json=body, headers=headers, timeout=self._timeout
                    )
                else:
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            self._url, json=body, headers=headers, timeout=self._timeout
                        )
                response.raise_for_status()
