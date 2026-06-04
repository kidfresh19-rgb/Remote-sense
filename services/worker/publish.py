"""Outbound publish worker (L7, Phase 6): build a farm's additive gateway payload from its stored
analyses and push it through the `GatewayPort`, recording the outcome in the outbox (published or
dead-letter). Idempotent at the DB level - an already-published payload is not re-pushed (R-2).
Geometry never leaves (invariant 6): the query selects canonical ids + stats only, never a
boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from rs_core import (
    Analysis,
    Farm,
    Field,
    get_outbox,
    published_narratives_for_farm,
    record_push,
)
from rs_sync import GatewayPort, IndexResult, PublishedNarrative, build_payload
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PublishSummary:
    """Outcome of one farm publish run."""

    canonical_farm_id: str
    results: int
    status: str  # published | dead_letter | empty | skipped


async def _farm_results(session: AsyncSession, canonical_farm_id: str) -> list[IndexResult]:
    """Every stored analysis for a farm as a publishable result, joined to each field's canonical
    id. Geometry is never selected."""
    rows = (
        await session.execute(
            select(Analysis, Field.canonical_field_id)
            .join(Field, Analysis.field_id == Field.id)
            .join(Farm, Field.farm_id == Farm.id)
            .where(Farm.canonical_farm_id == canonical_farm_id)
            .order_by(Analysis.pass_date, Analysis.index_name)
        )
    ).all()
    return [
        IndexResult.from_analysis(analysis, canonical_field_id=canonical_field_id)
        for analysis, canonical_field_id in rows
    ]


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
    payload = build_payload(canonical_farm_id, results, generated_at=when, narratives=narratives)
    existing = await get_outbox(session, idempotency_key=payload.idempotency_key)
    if existing is not None and existing.status == "published":
        return PublishSummary(
            canonical_farm_id=canonical_farm_id, results=len(results), status="skipped"
        )

    outcome = await gateway.push(payload)
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
    return PublishSummary(
        canonical_farm_id=canonical_farm_id, results=len(results), status=state.status
    )
