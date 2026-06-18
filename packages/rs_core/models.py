"""The remote-sense data model.

Ownership boundary (CLAUDE.md invariant 6): the gateway owns farm identity + geometry; we
ingest a read-only copy keyed by `canonical_farm_id` and own everything analytical. Geometry
is stored canonically in WGS84 (SRID 4326); the source CRS and the working UTM CRS travel
alongside so area math always reprojects (CLAUDE.md §2).

Reproducibility (invariant 5): every `Analysis` row carries its full provenance tuple
(provider, provider_scene_id, processing_mode, formula_version, geometry_version). Adding an
index is a config change, not a migration - the zonal-stats row is index-agnostic (PLAN §5).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    DDL,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rs_core.db import Base

_MULTIPOLYGON_4326 = Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=True)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Farm(Base):
    """A farm as received from the gateway. `canonical_farm_id` is the cross-system join key
    and is immutable once set; everything else is a refreshable copy."""

    __tablename__ = "farm"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    canonical_farm_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    agritrack_farmer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)

    boundary: Mapped[WKBElement | None] = mapped_column(_MULTIPOLYGON_4326, nullable=True)
    centroid_lon: Mapped[float] = mapped_column(Float)
    centroid_lat: Mapped[float] = mapped_column(Float)

    source_crs: Mapped[str] = mapped_column(String(32))
    working_crs: Mapped[str] = mapped_column(String(32))

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    fields: Mapped[list[Field]] = relationship(back_populates="farm", cascade="all, delete-orphan")


class Field(Base):
    """An inner field - the analysis unit. A farm with no fields gets one field derived from
    its boundary (DI-4), flagged `derived_from_farm`. `geometry_version` bumps on any genuine
    boundary change (DI-5); prior analyses keep their version tag and are retained."""

    __tablename__ = "field"
    __table_args__ = (
        UniqueConstraint("farm_id", "canonical_field_id", name="uq_field_farm_canonical"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farm.id", ondelete="CASCADE"), index=True
    )
    canonical_field_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    crop: Mapped[str | None] = mapped_column(String(64), nullable=True)

    boundary: Mapped[WKBElement] = mapped_column(_MULTIPOLYGON_4326)
    geometry_version: Mapped[int] = mapped_column(Integer, default=1)
    derived_from_farm: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set true on creation and whenever the boundary changes; the pipeline (Phase 3) clears
    # it once a fresh backfill against the new geometry completes.
    needs_backfill: Mapped[bool] = mapped_column(Boolean, default=True)

    source_crs: Mapped[str] = mapped_column(String(32))
    working_crs: Mapped[str] = mapped_column(String(32))

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    farm: Mapped[Farm] = relationship(back_populates="fields")
    geometry_versions: Mapped[list[FieldGeometryVersion]] = relationship(
        back_populates="field", cascade="all, delete-orphan"
    )


class FieldGeometryVersion(Base):
    """Immutable history of every boundary a field has ever had. An `Analysis` joins back to
    the exact geometry it was computed against via (field_id, geometry_version), so results
    stay reproducible after a boundary change (DI-5)."""

    __tablename__ = "field_geometry_version"
    __table_args__ = (UniqueConstraint("field_id", "version", name="uq_field_geometry_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    boundary: Mapped[WKBElement] = mapped_column(_MULTIPOLYGON_4326)
    area_m2: Mapped[float] = mapped_column(Float)

    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    field: Mapped[Field] = relationship(back_populates="geometry_versions")


class SceneMetadata(Base):
    """Per-scene radiometric metadata. The reflectance offset + quantification value are read
    from here, never hard-coded (CLAUDE.md invariant 2). Global to the system (not per-field)
    and immutable once ingested; upsert is keyed by scene_id."""

    __tablename__ = "scene_metadata"

    scene_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    quantification_value: Mapped[float] = mapped_column(Float)
    boa_add_offset: Mapped[dict] = mapped_column(JSONB)
    processing_baseline: Mapped[str | None] = mapped_column(String(32), nullable=True)
    crs: Mapped[str] = mapped_column(String(32))
    sensing_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scene_cloud_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Analysis(Base):
    """Index-agnostic zonal-stats row (PLAN §5). Populated by the analysis engine (Phase 2);
    the schema lands now so ingestion, provenance and idempotency are settled before any
    value is written. Uniqueness makes re-processing additive and idempotent.

    RANGE-partitioned by month on `pass_date` (S4.1; PRD 0001 R15/R18): scale is data volume -
    hundreds of thousands of farms accruing passes indefinitely, rows never deleted - so each
    month stays a small, separately-indexed table and date-bounded reads skip cold history.
    Postgres requires the partition key inside the primary key and every unique constraint,
    hence the composite key and the trailing column of `uq_analysis_identity`; the scientific
    identity is still the five leading columns, because `pass_date` is derived from the scene's
    immutable `sensing_datetime` - one scene, one date. Partitions: migration 0008 seeds the
    months around its run, the weekly `maintenance.ensure_analysis_partitions` task keeps the
    window rolling, and `analysis_default` (created with the parent, below) catches the rest."""

    __tablename__ = "analysis"
    __table_args__ = (
        UniqueConstraint(
            "field_id",
            "scene_id",
            "index_name",
            "geometry_version",
            "formula_version",
            "pass_date",
            name="uq_analysis_identity",
        ),
        # The workhorse read index: every hot path leads with field_id and orders or bounds
        # pass_date (timeseries, as-of resolution, audit, farm-level joins). scene_id carries
        # no index on purpose - no read path filters by scene alone, and scene_metadata rows
        # are immutable and never deleted, so the FK needs no supporting scan.
        Index("ix_analysis_field_index_date", "field_id", "index_name", "pass_date"),
        {"postgresql_partition_by": "RANGE (pass_date)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field.id", ondelete="CASCADE")
    )
    scene_id: Mapped[str] = mapped_column(String(256), ForeignKey("scene_metadata.scene_id"))
    pass_date: Mapped[date] = mapped_column(Date, primary_key=True)
    index_name: Mapped[str] = mapped_column(String(32))

    mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_val: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_val: Mapped[float | None] = mapped_column(Float, nullable=True)
    std: Mapped[float | None] = mapped_column(Float, nullable=True)
    p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    p90: Mapped[float | None] = mapped_column(Float, nullable=True)

    clear_fraction: Mapped[float] = mapped_column(Float)
    resolution_m: Mapped[float] = mapped_column(Float)

    # Provenance tuple (CLAUDE.md invariant 5).
    formula_version: Mapped[str] = mapped_column(String(32))
    geometry_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    provider_scene_id: Mapped[str] = mapped_column(String(256))
    processing_mode: Mapped[str] = mapped_column(String(32))

    cog_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# A partitioned parent holds no rows itself, so somewhere must own partition DDL. Deployed
# databases get monthly partitions from migration 0008 plus the weekly maintenance task;
# environments stood up by `Base.metadata.create_all` (the DB-gated tests) get this DEFAULT
# catch-all so inserts work with zero ceremony. Postgres drops it with the parent.
event.listen(
    Analysis.__table__,
    "after_create",
    DDL("CREATE TABLE IF NOT EXISTS analysis_default PARTITION OF analysis DEFAULT"),
)


class FieldCollectionState(Base):
    """Per-field, per-geometry-version pipeline cursor (Phase 3, D5). One row records how far
    collection has progressed for a field at a given boundary: whether the historical backfill is
    done, when the archive was last polled (the forward-fill cadence input), and the sensing-date
    watermark of the most recent pass already collected. Keyed per geometry_version because a
    boundary change (DI-5) is a fresh unit of work with its own backfill and its own cursor."""

    __tablename__ = "field_collection_state"
    __table_args__ = (
        UniqueConstraint("field_id", "geometry_version", name="uq_collection_state_field_geom"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field.id", ondelete="CASCADE"), index=True
    )
    geometry_version: Mapped[int] = mapped_column(Integer)

    backfill_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    backfill_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    backfill_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Forward-fill bookkeeping: when the archive was last polled (cadence, planning.
    # due_for_forward_fill) and the sensing date of the most recent pass already collected - the
    # watermark the next search starts from. The watermark only moves forward (advance_cursor).
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_scene_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Interpretation(Base):
    """A drafted plain-language agronomic read for one field/pass (Phase 4b, L4b). The model
    writes the `narrative`; `status` + `confidence` are grounded in the zonal stats, never the
    model. Stored unpublished and flagged for review - an agronomist edits and publishes (sets
    `published`); interpretation is never auto-published (risk #6). Keyed per prompt_version, so a
    prompt change re-drafts under a new identity instead of silently overwriting a reviewed read."""

    __tablename__ = "interpretation"
    __table_args__ = (
        UniqueConstraint(
            "field_id",
            "scene_id",
            "geometry_version",
            "prompt_version",
            name="uq_interpretation_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("scene_metadata.scene_id"), index=True
    )
    pass_date: Mapped[date] = mapped_column(Date, index=True)
    geometry_version: Mapped[int] = mapped_column(Integer)
    prompt_version: Mapped[str] = mapped_column(String(32))
    crop: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Grounding Context (weather/activity telemetry)
    gdd_accumulation: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_precipitation: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_activities: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # The model's words; the structured fields below are grounded in the numbers, not the model.
    narrative: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))

    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Annotation(Base):
    """A free-text note an analyst pins to a field, optionally to one pass - the shared,
    team-visible replacement for the former browser-local store. Written behind the RBAC
    `annotate` permission and read behind `view`. Pinned to a `geometry_version` (invariant 5)
    so a note keeps its meaning across a boundary change, and `author` is the verified token
    subject, never client input. Append-and-delete in v1; notes are not edited in place."""

    __tablename__ = "annotation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field.id", ondelete="CASCADE"), index=True
    )
    geometry_version: Mapped[int] = mapped_column(Integer)
    # Null = a whole-field note; a date pins it to one pass.
    pass_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncOutbox(Base):
    """One outbound gateway push, keyed by its idempotency key (L7, Phase 6). Records whether a
    farm's additive payload was delivered (`published`) or failed and awaits retry (`dead_letter`),
    so re-publishing the same result set is a DB-level no-op (R-2) and a failure is never lost. No
    geometry here - only the canonical farm id and push provenance (invariant 6)."""

    __tablename__ = "sync_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    canonical_farm_id: Mapped[str] = mapped_column(String(128), index=True)
    payload_version: Mapped[str] = mapped_column(String(32))
    result_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # pending | published | dead_letter
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RegionBoundaryLayer(Base):
    """A named layer of region boundaries (comparison groups, ADR 0010): the seeded Zimbabwe Natural
    Region map, or an analyst-uploaded ward / district / custom layer. Layer-level provenance is
    stamped here and version-stamped (invariant 5); `read_only` is set on the seeded layer so no
    upload or draw can edit or overwrite it. Unique on (source, year, version) so re-seeding the
    same published map - or replacing a candidate with the authoritative one - is idempotent."""

    __tablename__ = "region_boundary_layer"
    __table_args__ = (
        UniqueConstraint("source", "year", "version", name="uq_region_layer_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(256))
    # `source` here is the data provider / custodian (e.g. "ZINGSA"), distinct from the per-boundary
    # `RegionBoundary.source` creation method (seeded | uploaded | drawn) below.
    source: Mapped[str] = mapped_column(String(256))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[str] = mapped_column(String(64))
    publishing_authority: Mapped[str | None] = mapped_column(String(256), nullable=True)
    citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    naming_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    crs: Mapped[str] = mapped_column(String(32))
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acquisition_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    boundaries: Mapped[list[RegionBoundary]] = relationship(
        back_populates="layer", cascade="all, delete-orphan"
    )


class RegionBoundary(Base):
    """One region polygon within a layer (ADR 0010 + 2026-06-17 amendment). Every boundary - seeded,
    uploaded, or drawn - is the same reference-geometry category, tagged with its `source`
    (creation method) and `creator`. A boundary may span Natural Regions, so it carries a derived
    area-weighted `nr_composition` (e.g. {"Region III": 0.71}) and a `dominant_nr`,
    both recomputed when the geometry changes; a cross-zone boundary is never clipped or rejected.
    Geometry is canonical WGS84 (SRID 4326); area math reprojects to UTM at compute time (§2)."""

    __tablename__ = "region_boundary"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    layer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("region_boundary_layer.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(256))
    boundary: Mapped[WKBElement] = mapped_column(_MULTIPOLYGON_4326)
    # Creation method "seeded" | "uploaded" | "drawn" (RegionSource), stored as a string in the
    # house style (cf. SyncOutbox.status) and validated in code, never a native PG enum.
    source: Mapped[str] = mapped_column(String(16))
    creator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nr_composition: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict)
    dominant_nr: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    layer: Mapped[RegionBoundaryLayer] = relationship(back_populates="boundaries")


class FarmRegionAssignment(Base):
    """A farm's assignment to the region whose polygon contains its centroid, per layer (ADR 0010).
    Deterministic centroid point-in-polygon; `boundary_adjacent` flags a centroid within the
    configured edge tolerance for a sanity check. Keyed on the canonical farm id (invariant 6):
    unique on (canonical_farm_id, layer_id) so a farm has exactly one assignment per layer, stamped
    with `layer_version`. A re-survey or boundary edit re-runs the recompute, which upserts this row
    to the new boundary + version - a tracked re-assignment, never a silent overwrite."""

    __tablename__ = "farm_region_assignment"
    __table_args__ = (
        UniqueConstraint("canonical_farm_id", "layer_id", name="uq_farm_region_assignment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    canonical_farm_id: Mapped[str] = mapped_column(String(128), index=True)
    layer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("region_boundary_layer.id", ondelete="CASCADE"), index=True
    )
    region_boundary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("region_boundary.id", ondelete="CASCADE"), index=True
    )
    layer_version: Mapped[str] = mapped_column(String(64))
    boundary_adjacent: Mapped[bool] = mapped_column(Boolean, default=False)

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
