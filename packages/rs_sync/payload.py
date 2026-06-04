"""Versioned outbound payload for the gateway (L7, Phase 6).

GatewayPayload is our internal canonical push shape (confirmed behind the port, ADR 0006): canonical
farm id, per-field per-pass index results with full provenance, a payload version, and a
deterministic idempotency key. The AgriTrack wire record (POST /integrations/satellite/results) is
derived from this by the AgriTrackGatewayPort adapter (rs_sync.agritrack), so vendor specifics never
leak into the payload.

Split-ownership invariant (CLAUDE.md §1.6): results are pushed ADDITIVELY keyed on the canonical
farm id, and GEOMETRY IS NEVER RETURNED - there is deliberately no boundary/geometry field on any
model in this module."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Protocol

from pydantic import BaseModel


class AnalysisRow(Protocol):
    """The slice of a stored analysis row an `IndexResult` is built from. The ORM `Analysis`
    matches structurally; the mapping lives here so payload building stays free of an rs_core
    import. Note: no geometry attribute - geometry is never published."""

    index_name: str
    pass_date: date
    mean: float | None
    min_val: float | None
    max_val: float | None
    std: float | None
    p10: float | None
    p90: float | None
    clear_fraction: float
    confidence: str | None
    resolution_m: float
    formula_version: str
    provider: str
    provider_scene_id: str
    processing_mode: str


PAYLOAD_VERSION = "gw/v1"


class IndexResult(BaseModel):
    """One additively-publishable analysis result. Provenance travels (invariant 5); no geometry
    (invariant 6)."""

    canonical_field_id: str | None
    index_name: str
    pass_date: date
    mean: float | None
    min: float | None
    max: float | None
    std: float | None
    p10: float | None
    p90: float | None
    clear_fraction: float
    confidence: str | None
    resolution_m: float
    formula_version: str
    provider: str
    provider_scene_id: str
    processing_mode: str

    @classmethod
    def from_analysis(cls, row: AnalysisRow, *, canonical_field_id: str | None) -> IndexResult:
        """Map a stored analysis row onto a publishable result. Geometry is structurally absent."""
        return cls(
            canonical_field_id=canonical_field_id,
            index_name=row.index_name,
            pass_date=row.pass_date,
            mean=row.mean,
            min=row.min_val,
            max=row.max_val,
            std=row.std,
            p10=row.p10,
            p90=row.p90,
            clear_fraction=row.clear_fraction,
            confidence=row.confidence,
            resolution_m=row.resolution_m,
            formula_version=row.formula_version,
            provider=row.provider,
            provider_scene_id=row.provider_scene_id,
            processing_mode=row.processing_mode,
        )


class GatewayPayload(BaseModel):
    """The additive push for one farm: canonical id + its index results, versioned, with a
    deterministic idempotency key so a retried push is a safe no-op on the gateway (R-2)."""

    payload_version: str = PAYLOAD_VERSION
    canonical_farm_id: str
    generated_at: datetime
    results: list[IndexResult]
    idempotency_key: str


def _identity(result: IndexResult) -> str:
    return "|".join(
        [
            result.canonical_field_id or "",
            result.index_name,
            result.pass_date.isoformat(),
            result.formula_version,
            result.provider_scene_id,
        ]
    )


def compute_idempotency_key(canonical_farm_id: str, results: list[IndexResult]) -> str:
    """A stable key for (farm, this set of result identities). Re-building the same results yields
    the same key regardless of order, so the gateway can dedupe a retried push (R-2)."""
    parts = [canonical_farm_id, PAYLOAD_VERSION, *sorted(_identity(r) for r in results)]
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return f"{canonical_farm_id}:{digest[:32]}"


def build_payload(
    canonical_farm_id: str,
    results: list[IndexResult],
    *,
    generated_at: datetime | None = None,
) -> GatewayPayload:
    """Assemble the additive gateway payload for a farm. Geometry never enters (invariant 6)."""
    return GatewayPayload(
        canonical_farm_id=canonical_farm_id,
        generated_at=generated_at or datetime.now(UTC),
        results=results,
        idempotency_key=compute_idempotency_key(canonical_farm_id, results),
    )
