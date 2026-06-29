"""ward watch per-plot analysis

Revision ID: 0012_ward_watch_plot_analysis
Revises: 0011_ward_watch_household_plot
Create Date: 2026-06-29

Per-plot zonal index results for Ward Watch (PRD 0003 §4, backlog 0031). The Ward Watch analogue of
`analysis`, but keyed to a `plot` (our identity, invariant 6) rather than a gateway-owned `field`.
Every row carries the scene provenance tuple (invariant 5), the per-AOI `clear_fraction` (invariant
3), and the §4 pixel-quality honesty flag (`low_pixel_quality`). Non-partitioned in v1: Ward Watch
volume sits far below the farm fleet that drove `analysis` partitioning (S4.1); the unique identity
(plot, index, pass date, formula version) keeps the upsert additive and idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_ward_watch_plot_analysis"
down_revision: str | None = "0011_ward_watch_household_plot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The Natural Region a household centroid falls in, set by centroid assignment once plot
    # geometry exists (0031); carried for the 0032 cohort key.
    op.add_column("household", sa.Column("dominant_nr", sa.String(length=64), nullable=True))

    op.create_table(
        "plot_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", sa.String(length=256), nullable=False),
        sa.Column("pass_date", sa.Date(), nullable=False),
        sa.Column("index_name", sa.String(length=32), nullable=False),
        sa.Column("mean", sa.Float(), nullable=True),
        sa.Column("min_val", sa.Float(), nullable=True),
        sa.Column("max_val", sa.Float(), nullable=True),
        sa.Column("p10", sa.Float(), nullable=True),
        sa.Column("p90", sa.Float(), nullable=True),
        sa.Column("clear_fraction", sa.Float(), nullable=False),
        sa.Column("resolution_m", sa.Float(), nullable=False),
        sa.Column("pixels", sa.Integer(), nullable=False),
        sa.Column("low_pixel_quality", sa.Boolean(), nullable=False),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_scene_id", sa.String(length=256), nullable=False),
        sa.Column("processing_mode", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["plot_id"], ["plot.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "plot_id",
            "index_name",
            "pass_date",
            "formula_version",
            name="uq_plot_analysis_identity",
        ),
    )
    op.create_index(
        "ix_plot_analysis_plot_index_date",
        "plot_analysis",
        ["plot_id", "index_name", "pass_date"],
    )


def downgrade() -> None:
    op.drop_table("plot_analysis")
    op.drop_column("household", "dominant_nr")
