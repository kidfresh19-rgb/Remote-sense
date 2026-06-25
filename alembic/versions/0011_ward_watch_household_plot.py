"""ward watch household / plot / crop mix

Revision ID: 0011_ward_watch_household_plot
Revises: 0010_analysis_p5_p95
Create Date: 2026-06-25

Ward Watch enrollment model (PRD 0003 §8, backlog 0029). Communal plots are intercropped, so the
shape is Household -> Plot -> crop-mix matrix. These rows carry the gateway identity join key
(`canonical_household_id`) but are not a competing authority: the gateway stays identity owner
(invariant 6), and Ward Watch syncs enrollment to it. Plot geometry is canonical WGS84 (SRID 4326);
area math reprojects to the working UTM zone at compute time (CLAUDE.md §2). A ward is a region
boundary (ADR 0010), hence the nullable FK to region_boundary. Spatial index declared explicitly
with spatial_index=False on the column to avoid GeoAlchemy2 double-creating it (cf. 0001, 0009).
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_ward_watch_household_plot"
down_revision: str | None = "0010_analysis_p5_p95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MULTIPOLYGON = geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False)


def upgrade() -> None:
    op.create_table(
        "household",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_household_id", sa.String(length=128), nullable=True),
        sa.Column("ward_boundary_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ward_name", sa.String(length=256), nullable=True),
        sa.Column("village", sa.String(length=256), nullable=True),
        sa.Column("officer_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["ward_boundary_id"], ["region_boundary.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_household_client_uuid", "household", ["client_uuid"], unique=True)
    op.create_index(
        "ix_household_canonical_household_id", "household", ["canonical_household_id"], unique=True
    )
    op.create_index("ix_household_ward_boundary_id", "household", ["ward_boundary_id"])
    op.create_index("ix_household_officer_id", "household", ["officer_id"])

    op.create_table(
        "plot",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("household_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("boundary", _MULTIPOLYGON, nullable=False),
        sa.Column("geometry_source", sa.String(length=32), nullable=False),
        sa.Column("area_m2", sa.Float(), nullable=True),
        sa.Column("size_class", sa.String(length=32), nullable=True),
        sa.Column("planting_date", sa.Date(), nullable=True),
        sa.Column("planting_window", sa.String(length=32), nullable=True),
        sa.Column("dominant_crop", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_plot_client_uuid", "plot", ["client_uuid"], unique=True)
    op.create_index("ix_plot_household_id", "plot", ["household_id"])
    op.create_index("ix_plot_boundary", "plot", ["boundary"], postgresql_using="gist")

    op.create_table(
        "crop_mix_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crop", sa.String(length=64), nullable=False),
        sa.Column("weight_pct", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["plot_id"], ["plot.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plot_id", "crop", name="uq_crop_mix_plot_crop"),
    )
    op.create_index("ix_crop_mix_entry_plot_id", "crop_mix_entry", ["plot_id"])


def downgrade() -> None:
    op.drop_table("crop_mix_entry")
    op.drop_table("plot")
    op.drop_table("household")
