"""On-the-fly farm-level analytics: the health summary, the area-weighted index timeseries,
and anomaly detection on the latest pass. `rs_interpret` is imported inside the functions, never
at module level, so rs_core keeps no import-time upward dependency on the interpretation layer."""

from __future__ import annotations

from datetime import date
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rs_core.models import Analysis, Farm, Field, FieldGeometryVersion


class FarmAnalyticsSummary(TypedDict):
    canonical_farm_id: str
    farm_name: str
    region: str | None
    total_fields: int
    total_area_hectares: float
    crops: list[str]
    latest_pass_date: date | None
    overall_health: str | None
    overall_health_score: float | None
    field_status_counts: dict[str, int]


class FarmAnalyticsTimeSeriesPoint(TypedDict):
    pass_date: date
    scene_id: str
    area_weighted_mean: float
    clear_fraction: float
    analyzed_fields: int
    analyzed_area_hectares: float
    health_distribution_pct: dict[str, float] | None


class FarmAnomaly(TypedDict):
    field_id: str
    canonical_field_id: str | None
    name: str | None
    crop: str | None
    field_area_hectares: float
    index_name: str
    field_value: float
    farm_average: float
    status: str
    anomaly_type: str
    detail: str


class FarmAnalyticsAnomalies(TypedDict):
    pass_date: date | None
    farm_average: float | None
    anomalies: list[FarmAnomaly]


async def get_farm_analytics_summary(
    session: AsyncSession, canonical_farm_id: str
) -> FarmAnalyticsSummary | None:
    """Calculate overall farm health summary on-the-fly."""
    farm_info = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if not farm_info:
        return None

    fields_data = (
        await session.execute(
            select(Field.id, Field.crop, FieldGeometryVersion.area_m2)
            .outerjoin(
                FieldGeometryVersion,
                (FieldGeometryVersion.field_id == Field.id)
                & (FieldGeometryVersion.version == Field.geometry_version),
            )
            .where(Field.farm_id == farm_info.id)
        )
    ).all()

    total_fields = len(fields_data)
    total_area_m2 = sum(row.area_m2 for row in fields_data if row.area_m2 is not None)
    crops = sorted(list(set(row.crop for row in fields_data if row.crop)))

    latest_pass_date = (
        await session.execute(
            select(func.max(Analysis.pass_date))
            .join(Field, Analysis.field_id == Field.id)
            .where(Field.farm_id == farm_info.id)
        )
    ).scalar()

    overall_health = None
    overall_health_score = None
    field_status_counts = {"healthy": 0, "moderate": 0, "stressed": 0, "critical": 0}

    if latest_pass_date:
        analyses = (
            await session.execute(
                select(
                    Analysis.mean,
                    Field.crop,
                    FieldGeometryVersion.area_m2,
                    Field.canonical_field_id,
                )
                .join(Field, Analysis.field_id == Field.id)
                .outerjoin(
                    FieldGeometryVersion,
                    (FieldGeometryVersion.field_id == Field.id)
                    & (FieldGeometryVersion.version == Field.geometry_version),
                )
                .where(
                    Field.farm_id == farm_info.id,
                    Analysis.pass_date == latest_pass_date,
                    Analysis.index_name == "ndvi",
                )
            )
        ).all()

        total_weight_area = 0.0
        weighted_ndvi_sum = 0.0
        from rs_interpret import classify, vigour_to_status

        for mean, crop, area_m2, _ in analyses:
            area = area_m2 if area_m2 is not None else 1.0
            if mean is not None:
                weighted_ndvi_sum += mean * area
                total_weight_area += area
                vigour_band = classify("ndvi", mean, crop).label
                status = vigour_to_status(vigour_band)
                field_status_counts[status] += 1

        if total_weight_area > 0:
            overall_health_score = round(weighted_ndvi_sum / total_weight_area, 4)
            farm_vigour = classify("ndvi", overall_health_score).label
            overall_health = vigour_to_status(farm_vigour)

    return {
        "canonical_farm_id": canonical_farm_id,
        "farm_name": farm_info.name,
        "region": farm_info.region,
        "total_fields": total_fields,
        "total_area_hectares": round(total_area_m2 / 10000.0, 2),
        "crops": crops,
        "latest_pass_date": latest_pass_date,
        "overall_health": overall_health,
        "overall_health_score": overall_health_score,
        "field_status_counts": field_status_counts,
    }


async def get_farm_analytics_timeseries(
    session: AsyncSession,
    canonical_farm_id: str,
    index: str = "ndvi",
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[FarmAnalyticsTimeSeriesPoint] | None:
    """Calculate farm-level index timeseries."""
    farm_info = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if not farm_info:
        return None

    stmt = (
        select(
            Analysis.pass_date,
            Analysis.scene_id,
            Analysis.mean,
            Analysis.clear_fraction,
            Field.crop,
            FieldGeometryVersion.area_m2,
        )
        .join(Field, Analysis.field_id == Field.id)
        .outerjoin(
            FieldGeometryVersion,
            (FieldGeometryVersion.field_id == Field.id)
            & (FieldGeometryVersion.version == Field.geometry_version),
        )
        .where(Field.farm_id == farm_info.id, Analysis.index_name == index)
    )
    if start_date:
        stmt = stmt.where(Analysis.pass_date >= start_date)
    if end_date:
        stmt = stmt.where(Analysis.pass_date <= end_date)

    stmt = stmt.order_by(Analysis.pass_date.asc())
    rows = (await session.execute(stmt)).all()

    from collections import defaultdict

    grouped = defaultdict(list)
    for r in rows:
        grouped[(r.pass_date, r.scene_id)].append(r)

    points = []
    from rs_interpret import classify, vigour_to_status

    for (pass_date, scene_id), records in sorted(grouped.items(), key=lambda x: x[0][0]):
        total_area = 0.0
        weighted_val_sum = 0.0
        weighted_clear_sum = 0.0
        status_areas = {"healthy": 0.0, "moderate": 0.0, "stressed": 0.0, "critical": 0.0}

        for r in records:
            area = r.area_m2 if r.area_m2 is not None else 1.0
            if r.mean is not None:
                weighted_val_sum += r.mean * area
                weighted_clear_sum += r.clear_fraction * area
                total_area += area

                if index.lower() == "ndvi":
                    vigour_band = classify("ndvi", r.mean, r.crop).label
                    status = vigour_to_status(vigour_band)
                    status_areas[status] += area

        if total_area > 0:
            distribution_pct = {}
            if index.lower() == "ndvi":
                for k, v in status_areas.items():
                    distribution_pct[k] = round((v / total_area) * 100.0, 1)
            else:
                distribution_pct = None

            points.append(
                {
                    "pass_date": pass_date,
                    "scene_id": scene_id,
                    "area_weighted_mean": round(weighted_val_sum / total_area, 4),
                    "clear_fraction": round(weighted_clear_sum / total_area, 4),
                    "analyzed_fields": len(records),
                    "analyzed_area_hectares": round(total_area / 10000.0, 2),
                    "health_distribution_pct": distribution_pct,
                }
            )
    return points


async def get_farm_analytics_anomalies(
    session: AsyncSession,
    canonical_farm_id: str,
    deviation_threshold: float = 0.15,
) -> FarmAnalyticsAnomalies | None:
    """Identify underperforming fields or fields with sudden biomass drops on the latest pass."""
    farm_info = (
        await session.execute(select(Farm).where(Farm.canonical_farm_id == canonical_farm_id))
    ).scalar_one_or_none()
    if not farm_info:
        return None

    latest_pass_date = (
        await session.execute(
            select(func.max(Analysis.pass_date))
            .join(Field, Analysis.field_id == Field.id)
            .where(Field.farm_id == farm_info.id)
        )
    ).scalar()

    if not latest_pass_date:
        return {"pass_date": None, "farm_average": None, "anomalies": []}

    analyses = (
        await session.execute(
            select(
                Analysis.field_id,
                Analysis.mean,
                Field.canonical_field_id,
                Field.name,
                Field.crop,
                FieldGeometryVersion.area_m2,
            )
            .join(Field, Analysis.field_id == Field.id)
            .outerjoin(
                FieldGeometryVersion,
                (FieldGeometryVersion.field_id == Field.id)
                & (FieldGeometryVersion.version == Field.geometry_version),
            )
            .where(
                Field.farm_id == farm_info.id,
                Analysis.pass_date == latest_pass_date,
                Analysis.index_name == "ndvi",
            )
        )
    ).all()

    total_area = 0.0
    weighted_ndvi_sum = 0.0
    for row in analyses:
        area = row.area_m2 if row.area_m2 is not None else 1.0
        if row.mean is not None:
            weighted_ndvi_sum += row.mean * area
            total_area += area

    farm_mean = weighted_ndvi_sum / total_area if total_area > 0 else 0.0

    anomalies = []
    from rs_interpret import classify, vigour_to_status

    for row in analyses:
        if row.mean is None:
            continue

        is_underperforming = row.mean < (farm_mean - deviation_threshold)

        previous_pass = (
            await session.execute(
                select(Analysis.mean, Analysis.pass_date)
                .where(
                    Analysis.field_id == row.field_id,
                    Analysis.index_name == "ndvi",
                    Analysis.pass_date < latest_pass_date,
                )
                .order_by(Analysis.pass_date.desc())
                .limit(1)
            )
        ).first()

        is_sudden_drop = False
        drop_detail = ""
        if previous_pass and previous_pass.mean is not None:
            drop = previous_pass.mean - row.mean
            if drop >= 0.2:
                is_sudden_drop = True
                drop_detail = (
                    f"NDVI dropped by {round(drop, 2)} from "
                    f"{round(previous_pass.mean, 2)} on {previous_pass.pass_date.isoformat()}"
                )

        vigour_band = classify("ndvi", row.mean, row.crop).label
        status = vigour_to_status(vigour_band)

        if is_underperforming or is_sudden_drop:
            anomaly_types = []
            details = []
            if is_underperforming:
                anomaly_types.append("underperforming")
                details.append(
                    f"NDVI is {round(farm_mean - row.mean, 2)} below farm "
                    f"average ({round(farm_mean, 2)})"
                )
            if is_sudden_drop:
                anomaly_types.append("sudden_drop")
                details.append(drop_detail)

            anomalies.append(
                {
                    "field_id": str(row.field_id),
                    "canonical_field_id": row.canonical_field_id,
                    "name": row.name,
                    "crop": row.crop,
                    "field_area_hectares": round((row.area_m2 or 0.0) / 10000.0, 2),
                    "index_name": "ndvi",
                    "field_value": round(row.mean, 4),
                    "farm_average": round(farm_mean, 4),
                    "status": status,
                    "anomaly_type": "/".join(anomaly_types),
                    "detail": "; ".join(details),
                }
            )

    return {
        "pass_date": latest_pass_date,
        "farm_average": round(farm_mean, 4),
        "anomalies": anomalies,
    }
