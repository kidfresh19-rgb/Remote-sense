"""Outbound sync publishing utilities. Computes area-weighted farm-level averages dynamically
on-the-fly and joins them with field-level results to populate the gateway payload."""

from __future__ import annotations

from collections import defaultdict

from rs_core.models import Analysis, Farm, Field, FieldGeometryVersion
from rs_sync import IndexResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# A farm average is only as trustworthy as its least-clear contributing field, so the farm-level row
# carries the most conservative confidence of its inputs (engine.py grades high | medium | low).
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


async def fetch_farm_results_with_farm_averages(
    session: AsyncSession, canonical_farm_id: str
) -> list[IndexResult]:
    """Fetch all field-level index results for a farm, dynamically compute their area-weighted

    farm-level averages, and return the combined list (where canonical_field_id=None represents the

    farm-wide average).
    """
    rows = (
        await session.execute(
            select(
                Analysis,
                Field.canonical_field_id,
                FieldGeometryVersion.area_m2,
            )
            .join(Field, Analysis.field_id == Field.id)
            .join(Farm, Field.farm_id == Farm.id)
            .outerjoin(
                FieldGeometryVersion,
                (FieldGeometryVersion.field_id == Field.id)
                & (FieldGeometryVersion.version == Analysis.geometry_version),
            )
            .where(Farm.canonical_farm_id == canonical_farm_id)
            .order_by(Analysis.pass_date, Analysis.index_name)
        )
    ).all()

    # Field-level results
    field_results = [
        IndexResult.from_analysis(analysis, canonical_field_id=cfid) for analysis, cfid, _ in rows
    ]

    if not rows:
        return []

    # Group by the full provenance tuple (index, pass, provider, scene, formula, mode, resolution).
    grouped = defaultdict(list)
    for analysis, _, area_m2 in rows:
        area = area_m2 if area_m2 is not None else 1.0
        key = (
            analysis.index_name,
            analysis.pass_date,
            analysis.provider,
            analysis.provider_scene_id,
            analysis.formula_version,
            analysis.processing_mode,
            analysis.resolution_m,
        )
        grouped[key].append((analysis, area))

    # Calculate farm-level averages
    farm_results = []
    for key, items in grouped.items():
        (
            index_name,
            pass_date,
            provider,
            provider_scene_id,
            formula_version,
            processing_mode,
            resolution_m,
        ) = key

        total_area = 0.0
        mean_area = 0.0
        std_area = 0.0
        weighted_mean_sum = 0.0
        weighted_std_sum = 0.0
        weighted_clear_sum = 0.0

        min_vals = []
        max_vals = []
        p10_vals = []
        p90_vals = []
        confidence_vals = []

        for analysis, area in items:
            total_area += area
            weighted_clear_sum += (analysis.clear_fraction or 0.0) * area
            if analysis.confidence:
                confidence_vals.append(analysis.confidence)

            if analysis.mean is not None:
                weighted_mean_sum += analysis.mean * area
                mean_area += area
            if analysis.std is not None:
                weighted_std_sum += analysis.std * area
                std_area += area
            if analysis.min_val is not None:
                min_vals.append(analysis.min_val)
            if analysis.max_val is not None:
                max_vals.append(analysis.max_val)
            if analysis.p10 is not None:
                p10_vals.append(analysis.p10)
            if analysis.p90 is not None:
                p90_vals.append(analysis.p90)

        if total_area > 0:
            # Area-weight each statistic over the fields that actually reported it, and guard on
            # that contributing area - never on the sign of the sum. NDWI/NDRE/NDMI are routinely
            # negative, so a `sum > 0` guard would silently null out valid farm-level means.
            mean_val = round(weighted_mean_sum / mean_area, 4) if mean_area > 0 else None
            std_val = round(weighted_std_sum / std_area, 4) if std_area > 0 else None
            clear_frac = round(weighted_clear_sum / total_area, 4)
            min_val = min(min_vals) if min_vals else None
            max_val = max(max_vals) if max_vals else None
            p10_val = min(p10_vals) if p10_vals else None
            p90_val = max(p90_vals) if p90_vals else None
            confidence = (
                max(confidence_vals, key=lambda c: _CONFIDENCE_RANK.get(c, 1))
                if confidence_vals
                else "high"
            )

            farm_results.append(
                IndexResult(
                    canonical_field_id=None,  # None represents the farm scope
                    index_name=index_name,
                    pass_date=pass_date,
                    mean=mean_val,
                    min=min_val,
                    max=max_val,
                    std=std_val,
                    p10=p10_val,
                    p90=p90_val,
                    clear_fraction=clear_frac,
                    confidence=confidence,
                    resolution_m=resolution_m,
                    formula_version=formula_version,
                    provider=provider,
                    provider_scene_id=provider_scene_id,
                    processing_mode=processing_mode,
                )
            )

    return field_results + farm_results
