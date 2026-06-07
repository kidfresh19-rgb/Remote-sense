"""Outbound publish worker (L7, Phase 6): build a farm's additive gateway payload from its stored
analyses and push it through the `GatewayPort`, recording the outcome in the outbox (published or
dead-letter). Idempotent at the DB level - an already-published payload is not re-pushed (R-2).
Geometry never leaves (invariant 6): the query selects canonical ids + stats only, never a
boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from rs_core import get_outbox, published_narratives_for_farm, record_push
from rs_core.config import GatewayAdapter, Settings
from rs_core.logging import get_logger
from rs_sync import (
    AgriTrackGatewayPort,
    GatewayPort,
    HttpGatewayPort,
    IndexResult,
    PublishedNarrative,
    PushResult,
    RecordingGatewayPort,
    build_payload,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.worker.publish_utils import fetch_farm_results_with_farm_averages

log = get_logger("sync.publish")

# Non-empty values in .env that still mean "not configured yet". Treating them as unset turns a
# silent no-op (or a confusing DNS failure) into a clear, actionable config message.
_PLACEHOLDER_MARKERS = ("your-ngrok-url", "your-agritrack", "changeme", "example.com")


def _is_unset(value: str | None) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _bad_url(value: str | None) -> str | None:
    """None if `value` is a usable absolute http(s) URL, else a short reason. Catches the two
    misconfigurations that would otherwise only surface as a runtime push failure: a placeholder
    host, and a URL with no scheme (httpx cannot POST to a bare `host/path`)."""
    if _is_unset(value):
        return "unset or a placeholder"
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in ("http", "https"):
        return "missing an http(s):// scheme"
    if not parsed.netloc:
        return "missing a host"
    return None


def gateway_config_error(settings: Settings) -> str | None:
    """Why the configured gateway cannot deliver a real push, or None if it can. The `recording`
    dry-run returns None - it is a valid no-op, not a misconfiguration. Only an adapter meant to
    leave the building (`agritrack` / `http`) with a missing, placeholder, or malformed URL (or a
    missing key) is an error. The publish endpoint calls this so a doomed push fails fast with a
    clear message instead of enqueuing a task that constructs nothing and leaves the workspace
    polling forever."""
    adapter = settings.gateway_adapter
    if adapter is GatewayAdapter.AGRITRACK:
        reason = _bad_url(settings.agritrack_base_url)
        if reason:
            return f"adapter is 'agritrack' but RS_AGRITRACK_BASE_URL is {reason}"
        if not settings.agritrack_api_key.strip():
            return "adapter is 'agritrack' but RS_AGRITRACK_API_KEY is unset"
    elif adapter is GatewayAdapter.HTTP:
        reason = _bad_url(settings.gateway_push_url)
        if reason:
            return f"adapter is 'http' but RS_GATEWAY_PUSH_URL is {reason}"
    return None


def gateway_is_dry_run(settings: Settings) -> bool:
    """True when the active gateway records 'published' without sending anything (the `recording`
    sink, or a real-push adapter that has degraded to it). Surfaced so the workspace can show
    'Recorded (dry-run)' rather than a misleading 'Sent'."""
    if settings.gateway_adapter is GatewayAdapter.AGRITRACK:
        return False
    if settings.gateway_adapter is GatewayAdapter.HTTP and settings.gateway_push_url:
        return False
    return True


def gateway_from_settings(settings: Settings) -> GatewayPort:
    """The active GatewayPort, selected by RS_GATEWAY_ADAPTER (ADR 0006): the AgriTrack push to
    /integrations/satellite/results, a generic HTTP push, or a recording dry-run sink (default).
    The single source of truth for gateway selection, shared by the worker and the API's fail-fast
    check. Construction of a real-push adapter still validates its own creds; pre-screen with
    `gateway_config_error` to report a misconfiguration rather than raise here."""
    adapter = settings.gateway_adapter
    if adapter is GatewayAdapter.AGRITRACK:
        return AgriTrackGatewayPort(
            settings.agritrack_base_url,
            settings.agritrack_api_key,
            max_concurrency=settings.gateway_max_concurrency,
        )
    if adapter is GatewayAdapter.HTTP and settings.gateway_push_url:
        return HttpGatewayPort(settings.gateway_push_url, settings.gateway_auth_token)
    return RecordingGatewayPort()


@dataclass(frozen=True)
class PublishSummary:
    """Outcome of one farm publish run."""

    canonical_farm_id: str
    results: int
    status: str  # published | dead_letter | empty | skipped


async def _farm_results(session: AsyncSession, canonical_farm_id: str) -> list[IndexResult]:
    """Every stored analysis for a farm as a publishable result, joined to each field's canonical
    id. Geometry is never selected. Calculates area-weighted farm averages and appends them."""
    return await fetch_farm_results_with_farm_averages(session, canonical_farm_id)


async def _farm_narratives(
    session: AsyncSession, canonical_farm_id: str
) -> list[PublishedNarrative]:
    """The farm's published agronomic reads, carried on the payload so the gateway attaches them
    (risk #6: only published reads leave). Geometry is never selected (invariant 6)."""
    return [
        PublishedNarrative(canonical_field_id=cfid, pass_date=pass_date, narrative=narrative)
        for cfid, pass_date, narrative in await published_narratives_for_farm(
            session, canonical_farm_id
        )
    ]


async def publish_farm(
    session: AsyncSession,
    gateway: GatewayPort,
    *,
    canonical_farm_id: str,
    now: datetime | None = None,
) -> PublishSummary:
    """Build + push one farm's additive payload, recording the outcome. Skips the push entirely if
    this exact result set was already published (R-2). Returns a summary; the caller commits."""
    when = now or datetime.now(UTC)
    results = await _farm_results(session, canonical_farm_id)
    if not results:
        return PublishSummary(canonical_farm_id=canonical_farm_id, results=0, status="empty")

    narratives = await _farm_narratives(session, canonical_farm_id)
    payload = build_payload(
        canonical_farm_id,
        results,
        generated_at=when,
        narratives=narratives,
        destination=gateway.destination_key(),
    )
    existing = await get_outbox(session, idempotency_key=payload.idempotency_key)
    if existing is not None and existing.status == "published":
        return PublishSummary(
            canonical_farm_id=canonical_farm_id, results=len(results), status="skipped"
        )

    # The adapters convert known delivery failures (transport, HTTP, bad farm id) into an ok=False
    # PushResult; this guard is the backstop for anything unexpected, so a publish always lands a
    # terminal outbox state (published | dead_letter) instead of crashing the task and leaving the
    # workspace polling "Sending..." with nothing to show. The payload key already exists here, so
    # the dead-letter is recorded against the same idempotency key a retry will reuse.
    try:
        outcome = await gateway.push(payload)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: never lose a push to a crash
        outcome = PushResult(ok=False, status="error", detail=f"unexpected push error: {exc}")
    state = await record_push(
        session,
        idempotency_key=payload.idempotency_key,
        canonical_farm_id=canonical_farm_id,
        payload_version=payload.payload_version,
        result_count=len(results),
        ok=outcome.ok,
        detail=outcome.detail,
        pushed_at=when,
    )
    # One structured line per push so a real delivery is auditable: which farm, how many records,
    # where it went, and the outcome. A dead-letter logs at warning with the reason the operator
    # needs; a success stays at info.
    fields = {
        "canonical_farm_id": canonical_farm_id,
        "results": len(results),
        "destination": gateway.destination_key(),
        "attempts": state.attempts,
        "idempotency_key": payload.idempotency_key,
    }
    if state.status == "dead_letter":
        log.warning("gateway.push.dead_letter", detail=outcome.detail, **fields)
    else:
        log.info("gateway.push.published", **fields)
    return PublishSummary(
        canonical_farm_id=canonical_farm_id, results=len(results), status=state.status
    )
