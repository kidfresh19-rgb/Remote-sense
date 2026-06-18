"""region clusters: region_boundary_layer, region_boundary, farm_region_assignment

Revision ID: 0009_region_clusters
Revises: 0008_partition_analysis
Create Date: 2026-06-18

Comparison-groups foundation (PRD 0002 slice 1, ADR 0010). Region boundaries are remote-sense-owned
analytical reference geometry, distinct from gateway-owned farm-identity geometry and never pushed
(invariant 6). Geometry is canonical WGS84 (SRID 4326); area/distance math reprojects to the working
UTM zone at compute time (CLAUDE.md §2). The seeded Natural Region layer is read_only and idempotent
on (source, year, version). Spatial indexes are declared explicitly with spatial_index=False on the
column to avoid GeoAlchemy2 double-creating them during op.create_table (cf. 0001).
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_region_clusters"
down_revision: str | None = "0008_partition_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MULTIPOLYGON = geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False)


def upgrade() -> None:
    op.create_table(
        "region_boundary_layer",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("publishing_authority", sa.String(length=256), nullable=True),
        sa.Column("citation", sa.Text(), nullable=True),
        sa.Column("naming_column", sa.String(length=64), nullable=True),
        sa.Column("crs", sa.String(length=32), nullable=False),
        sa.Column("acquisition_date", sa.Date(), nullable=True),
        sa.Column("acquisition_path", sa.String(length=256), nullable=True),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source", "year", "version", name="uq_region_layer_identity"),
    )

    op.create_table(
        "region_boundary",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("layer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("boundary", _MULTIPOLYGON, nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("creator", sa.String(length=128), nullable=True),
        sa.Column("nr_composition", postgresql.JSONB(), nullable=False),
        sa.Column("dominant_nr", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["layer_id"], ["region_boundary_layer.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_region_boundary_layer_id", "region_boundary", ["layer_id"])
    op.create_index(
        "ix_region_boundary_boundary", "region_boundary", ["boundary"], postgresql_using="gist"
    )

    op.create_table(
        "farm_region_assignment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_farm_id", sa.String(length=128), nullable=False),
        sa.Column("layer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region_boundary_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("layer_version", sa.String(length=64), nullable=False),
        sa.Column("boundary_adjacent", sa.Boolean(), nullable=False),
        sa.Column(
            "assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["layer_id"], ["region_boundary_layer.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_boundary_id"], ["region_boundary.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("canonical_farm_id", "layer_id", name="uq_farm_region_assignment"),
    )
    op.create_index(
        "ix_farm_region_assignment_canonical_farm_id",
        "farm_region_assignment",
        ["canonical_farm_id"],
    )
    op.create_index("ix_farm_region_assignment_layer_id", "farm_region_assignment", ["layer_id"])
    op.create_index(
        "ix_farm_region_assignment_region_boundary_id",
        "farm_region_assignment",
        ["region_boundary_id"],
    )


def downgrade() -> None:
    op.drop_table("farm_region_assignment")
    op.drop_table("region_boundary")
    op.drop_table("region_boundary_layer")
