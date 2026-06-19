"""AgriTrack outbound adapter (ADR 0006): the `GatewayPort` that delivers analysis results to
AgriTrack's `POST /integrations/satellite/results`.

It aggregates our per-index results into one record per (field, analysis date) with the contract's
flattened metrics, decodes the canonical ids back to AgriTrack integers (farm "2", field "4",
sub-plot "4.1"), and posts each with the `X-Api-Key` header. Sub-plots for the same (field, date)
are now grouped into a single POST with a `subPlots` array (gateway spec 2026-06-18); field and
farm-scope records remain flat. All AgriTrack-specific shape (endpoint, auth, metric names, the
classification mapping) lives here (invariant 1), and geometry never leaves (invariant 6)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date

import httpx
from pydantic import BaseModel

from rs_sync.payload import GatewayPayload, IndexResult
from rs_sync.port import GatewayPort, PushResult
from rs_sync.resilience import (
    GATEWAY_PUSH_ERRORS,
    describe_push_error,
    push_retrying,
)

_RESULTS_PATH = "/integrations/satellite/results"

# classification -> the contract's stress_level (their side maps stress_level onto classification).
_STRESS = {"healthy": "none", "moderate": "low", "stressed": "moderate", "critical": "high"}


class SatelliteMetrics(BaseModel):
    """The flattened per-record metrics AgriTrack expects (ADR 0006). `savi_mean`/`ndre_mean` were
    added 2026-06-19 (ADR 0006 §3 amendment): both indices were already computed and stored, but
    had no wire field here, so they were dropped at this adapter and never reached AgriTrack."""

    ndvi_mean: float | None = None
    ndvi_min: float | None = None
    ndvi_max: float | None = None
    evi_mean: float | None = None
    savi_mean: float | None = None
    ndre_mean: float | None = None
    ndwi_mean: float | None = None
    cloud_cover_pct: float | None = None
    health_score: float | None = None
    classification: str | None = None


class SatelliteInterpretation(BaseModel):
    """The optional interpretation block: none|low|moderate|high stress, free-text anomalies, and
    `notes` - the agronomist's reviewed, published narrative (ADR 0006). `notes` is present only
    when the read was published; an unreviewed draft never reaches the wire (risk #6)."""

    stress_level: str | None = None
    anomalies: list[str] = []
    notes: str | None = None


class SubPlotEntry(BaseModel):
    """One sub-plot within a grouped `scope: sub_plot` record (gateway spec 2026-06-18)."""

    subPlotId: int
    extId: str
    metrics: SatelliteMetrics


class SatelliteResult(BaseModel):
    """One AgriTrack `/integrations/satellite/results` record.

    For `scope in ("farm", "field")`: flat record with top-level `extId`, `metrics`,
    `interpretation`.  For `scope == "sub_plot"`: grouped record with a `subPlots` array; the
    flat fields are absent (excluded from the JSON body). Field names are the contract's camelCase
    wire keys."""

    sourceSystem: str = "satellite"
    farmId: int
    fieldId: int | None = None
    subPlotId: int | None = None
    scope: str
    analysisDate: str
    # flat-scope (field / farm) fields
    extId: str | None = None
    metrics: SatelliteMetrics | None = None
    interpretation: SatelliteInterpretation | None = None
    # sub_plot grouping (gateway spec 2026-06-18)
    subPlots: list[SubPlotEntry] | None = None


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


def _build_metrics(rows: list[IndexResult]) -> SatelliteMetrics:
    """Aggregate one (field-or-subplot, date)'s per-index rows into the flat metrics block.
    Shared by flat field records and grouped sub-plot entries."""
    # lazy import keeps rs_sync import-light
    from rs_interpret import classify, vigour_to_status

    by_index = {r.index_name.lower(): r for r in rows}
    ndvi = by_index.get("ndvi")
    evi2 = by_index.get("evi2")
    savi = by_index.get("savi")
    ndre = by_index.get("ndre")
    ndmi = by_index.get("ndmi")
    metrics = SatelliteMetrics(
        ndvi_mean=ndvi.mean if ndvi else None,
        ndvi_min=ndvi.min if ndvi else None,
        ndvi_max=ndvi.max if ndvi else None,
        evi_mean=evi2.mean if evi2 else None,
        savi_mean=savi.mean if savi else None,
        ndre_mean=ndre.mean if ndre else None,
        ndwi_mean=ndmi.mean if ndmi else None,
        cloud_cover_pct=round((1.0 - rows[0].clear_fraction) * 100.0, 1),
    )
    if ndvi is not None and ndvi.mean is not None:
        classification = vigour_to_status(classify("ndvi", ndvi.mean).label)
        metrics.classification = classification
        metrics.health_score = round(_clamp01(ndvi.mean), 2)
    return metrics


def _build_record(
    farm_id: int,
    canonical_field_id: str | None,
    pass_date: date,
    rows: list[IndexResult],
    narrative: str | None = None,
) -> SatelliteResult:
    """Build a flat farm- or field-scope record. NDMI fills `ndwi_mean` (ADR 0006);
    `classification`/`health_score` come from the NDVI vigour band; `cloud_cover_pct` is the
    AOI's non-clear fraction. `narrative` is the published agronomist read, attached as
    `interpretation.notes` (only published reads are passed in, risk #6)."""
    metrics = _build_metrics(rows)
    stress_level: str | None = None
    if metrics.classification is not None:
        stress_level = _STRESS.get(metrics.classification)
    # Build the block (always return a valid dictionary, never null, to satisfy the API contract)
    interpretation = SatelliteInterpretation(stress_level=stress_level, notes=narrative)
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


def _build_sub_plot_group(
    farm_id: int,
    field_id: int,
    pass_date: date,
    sub_plots_by_id: dict[int, list[IndexResult]],
) -> SatelliteResult:
    """Bundle all sub-plots for one (field, date) into a single grouped record (gateway spec
    2026-06-18). Each sub-plot entry carries its own metrics; no interpretation block is emitted
    at this level since the gateway spec does not include one."""
    entries = [
        SubPlotEntry(
            subPlotId=sub_plot_id,
            extId=f"{farm_id}:sub:{sub_plot_id}:{pass_date.isoformat()}",
            metrics=_build_metrics(rows),
        )
        for sub_plot_id, rows in sorted(sub_plots_by_id.items())
    ]
    return SatelliteResult(
        farmId=farm_id,
        fieldId=field_id,
        scope="sub_plot",
        analysisDate=pass_date.isoformat(),
        subPlots=entries,
    )


def to_satellite_results(payload: GatewayPayload) -> list[SatelliteResult]:
    """Aggregate a farm's per-index results into AgriTrack records.

    Field- and farm-scope entries produce one flat `SatelliteResult` each (with `extId`, `metrics`,
    `interpretation`). Sub-plot entries are grouped by `(parent_field_id, analysis_date)` into one
    record per group with a `subPlots` array (gateway spec 2026-06-18). Published narratives are
    attached to field/farm flat records only. Pure and order-stable, so a re-push is byte-identical.
    Unit-tested with synthetic results."""
    try:
        farm_id = int(payload.canonical_farm_id)
    except ValueError as exc:
        raise ValueError(
            f"AgriTrack farmId must be an integer canonical_farm_id, got "
            f"{payload.canonical_farm_id!r}"
        ) from exc

    narratives = {(n.canonical_field_id, n.pass_date): n.narrative for n in payload.interpretations}

    # Group rows by (canonical_field_id, pass_date) - same as before.
    by_field_date: dict[tuple[str | None, date], list[IndexResult]] = defaultdict(list)
    for result in payload.results:
        by_field_date[(result.canonical_field_id, result.pass_date)].append(result)

    flat_records: list[SatelliteResult] = []
    # sub_plot_groups: {(parent_field_id, pass_date): {sub_plot_id: rows}}
    sub_plot_groups: dict[tuple[int, date], dict[int, list[IndexResult]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for (canonical_field_id, pass_date), rows in by_field_date.items():
        field_id, sub_plot_id, scope = _decode_field(canonical_field_id)
        if scope == "sub_plot" and field_id is not None and sub_plot_id is not None:
            sub_plot_groups[(field_id, pass_date)][sub_plot_id].extend(rows)
        else:
            narrative = narratives.get((canonical_field_id, pass_date))
            flat_records.append(
                _build_record(farm_id, canonical_field_id, pass_date, rows, narrative)
            )

    sub_plot_records = [
        _build_sub_plot_group(farm_id, field_id, pass_date, sub_plots_by_id)
        for (field_id, pass_date), sub_plots_by_id in sub_plot_groups.items()
    ]

    records = flat_records + sub_plot_records
    records.sort(key=lambda s: (s.fieldId or -1, s.analysisDate, s.scope))
    return records


class AgriTrackGatewayPort(GatewayPort):
    """Deliver results to AgriTrack's `/integrations/satellite/results` (ADR 0006): one POST per
    (field, date) record (flat for field scope; grouped for sub_plot scope), authenticated with
    `X-Api-Key`, transient failures retried. `extId` deduplicates on AgriTrack's side; the
    `SyncOutbox` dedupes the whole payload on ours (R-2), so a retried push that re-sends
    already-delivered records is safe."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        max_attempts: int = 4,
        backoff: float = 0.5,
        max_concurrency: int = 10,
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
        self._max_concurrency = max_concurrency

    def destination_key(self) -> str:
        return self._url

    async def push(self, payload: GatewayPayload) -> PushResult:
        try:
            records = to_satellite_results(payload)
        except ValueError as exc:
            # A non-integer canonical_farm_id can never satisfy the AgriTrack contract, so this is a
            # permanent failure of THIS farm, not a transient one. Dead-letter it with the reason
            # instead of raising - a raise here crashes the publish task and leaves the workspace
            # polling "Sending..." forever with nothing to show.
            return PushResult(ok=False, status="error", detail=str(exc))
        if not records:
            return PushResult(ok=True, status="empty")

        # When a client is injected (tests), use it as-is. Otherwise own one client for the whole
        # batch - reused across every record and every retry - instead of opening a fresh
        # connection per POST. A cold tunnel (ngrok) gets a bounded connect timeout, and the pool is
        # capped to the same concurrency the semaphore allows.
        if self._client is not None:
            return await self._push_records(self._client, records)
        timeout = httpx.Timeout(self._timeout, connect=min(self._timeout, 10.0))
        limits = httpx.Limits(max_connections=self._max_concurrency)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            return await self._push_records(client, records)

    async def _push_records(
        self, client: httpx.AsyncClient, records: list[SatelliteResult]
    ) -> PushResult:
        # A farm's whole batch is delivered as one POST per record, all to the single
        # /integrations/satellite/results endpoint the contract defines (ADR 0006 §2 - there is no
        # bulk endpoint). Posts run under a concurrency bound so a large farm goes at once without
        # flooding AgriTrack.
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _post_with_sem(record: SatelliteResult) -> None:
            async with semaphore:
                await self._post(client, record)

        # return_exceptions lets every post finish even when one fails, so no in-flight request is
        # left orphaned mid-batch. A push error (transient exhausted, or a permanent 4xx that failed
        # fast) dead-letters the whole push - safely re-sent whole via extId + outbox dedup;
        # anything unexpected propagates so a real bug surfaces, not silently.
        outcomes = await asyncio.gather(
            *(_post_with_sem(r) for r in records), return_exceptions=True
        )
        unexpected = [
            o
            for o in outcomes
            if isinstance(o, BaseException) and not isinstance(o, GATEWAY_PUSH_ERRORS)
        ]
        if unexpected:
            raise unexpected[0]
        failures = [o for o in outcomes if isinstance(o, GATEWAY_PUSH_ERRORS)]
        if failures:
            # Report the failure ratio plus the first reason, so a partial batch failure is legible
            # ("3/10 records failed") rather than a lone error stripped of its scale. The whole
            # farm re-pushes on retry (extId + outbox dedup), so the first reason is sufficient.
            detail = (
                f"{len(failures)}/{len(records)} records failed; "
                f"first: {describe_push_error(failures[0])}"
            )
            return PushResult(ok=False, status="error", detail=detail)
        return PushResult(ok=True, status="ok", detail=f"{len(records)} records")

    async def _post(self, client: httpx.AsyncClient, record: SatelliteResult) -> None:
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json"}
        # exclude_none keeps the wire body clean: sub_plot grouped records omit extId/metrics/
        # interpretation (None); field records omit subPlots (None) and any absent metric fields.
        body = record.model_dump(mode="json", exclude_none=True)
        async for attempt in push_retrying(max_attempts=self._max_attempts, backoff=self._backoff):
            with attempt:
                response = await client.post(
                    self._url, json=body, headers=headers, timeout=self._timeout
                )
                response.raise_for_status()
