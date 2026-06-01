"""initial schema: farm, field, field_geometry_version, scene_metadata, analysis

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-31

Phase 1 data model. Geometry is stored canonically in WGS84 (SRID 4326); area math reprojects
to the working UTM zone at compute time (CLAUDE.md §2). Spatial indexes are declared
explicitly here with `spatial_index=False` on the columns to avoid GeoAlchemy2 double-creating
them during op.create_table.
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MULTIPOLYGON = geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "farm",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_farm_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("boundary", _MULTIPOLYGON, nullable=True),
        sa.Column("centroid_lon", sa.Float(), nullable=False),
        sa.Column("centroid_lat", sa.Float(), nullable=False),
        sa.Column("source_crs", sa.String(length=32), nullable=False),
        sa.Column("working_crs", sa.String(length=32), nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("canonical_farm_id", name="uq_farm_canonical_farm_id"),
    )
    op.create_index("ix_farm_canonical_farm_id", "farm", ["canonical_farm_id"])
    op.create_index("ix_farm_boundary", "farm", ["boundary"], postgresql_using="gist")

    op.create_table(
        "field",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_field_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("crop", sa.String(length=64), nullable=True),
        sa.Column("boundary", _MULTIPOLYGON, nullable=False),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("derived_from_farm", sa.Boolean(), nullable=False),
        sa.Column("needs_backfill", sa.Boolean(), nullable=False),
        sa.Column("source_crs", sa.String(length=32), nullable=False),
        sa.Column("working_crs", sa.String(length=32), nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farm.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("farm_id", "canonical_field_id", name="uq_field_farm_canonical"),
    )
    op.create_index("ix_field_farm_id", "field", ["farm_id"])
    op.create_index("ix_field_boundary", "field", ["boundary"], postgresql_using="gist")

    op.create_table(
        "field_geometry_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("boundary", _MULTIPOLYGON, nullable=False),
        sa.Column("area_m2", sa.Float(), nullable=False),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["field_id"], ["field.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("field_id", "version", name="uq_field_geometry_version"),
    )
    op.create_index("ix_field_geometry_version_field_id", "field_geometry_version", ["field_id"])
    op.create_index(
        "ix_field_geometry_version_boundary",
        "field_geometry_version",
        ["boundary"],
        postgresql_using="gist",
    )

    op.create_table(
        "scene_metadata",
        sa.Column("scene_id", sa.String(length=256), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("quantification_value", sa.Float(), nullable=False),
        sa.Column("boa_add_offset", postgresql.JSONB(), nullable=False),
        sa.Column("processing_baseline", sa.String(length=32), nullable=True),
        sa.Column("crs", sa.String(length=32), nullable=False),
        sa.Column("sensing_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scene_cloud_pct", sa.Float(), nullable=True),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", sa.String(length=256), nullable=False),
        sa.Column("pass_date", sa.Date(), nullable=False),
        sa.Column("index_name", sa.String(length=32), nullable=False),
        sa.Column("mean", sa.Float(), nullable=True),
        sa.Column("min_val", sa.Float(), nullable=True),
        sa.Column("max_val", sa.Float(), nullable=True),
        sa.Column("std", sa.Float(), nullable=True),
        sa.Column("p10", sa.Float(), nullable=True),
        sa.Column("p90", sa.Float(), nullable=True),
        sa.Column("clear_fraction", sa.Float(), nullable=False),
        sa.Column("resolution_m", sa.Float(), nullable=False),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_scene_id", sa.String(length=256), nullable=False),
        sa.Column("processing_mode", sa.String(length=32), nullable=False),
        sa.Column("cog_uri", sa.String(length=512), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["field_id"], ["field.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scene_metadata.scene_id"]),
        sa.UniqueConstraint(
            "field_id",
            "scene_id",
            "index_name",
            "geometry_version",
            "formula_version",
            name="uq_analysis_identity",
        ),
    )
    op.create_index("ix_analysis_field_id", "analysis", ["field_id"])
    op.create_index("ix_analysis_scene_id", "analysis", ["scene_id"])
    op.create_index("ix_analysis_pass_date", "analysis", ["pass_date"])
    op.create_index("ix_analysis_index_name", "analysis", ["index_name"])


def downgrade() -> None:
    op.drop_table("analysis")
    op.drop_table("scene_metadata")
    op.drop_table("field_geometry_version")
    op.drop_table("field")
    op.drop_table("farm")
