"""add p5 and p95 percentile columns to analysis

Revision ID: 0010_analysis_p5_p95
Revises: 0009_region_clusters
Create Date: 2026-06-18

The validation matrix comparison with the Copernicus Browser revealed that remote-sense was
storing only p10/p90 while the Browser Statistical tool reports p5/p95. Adding both outer
percentiles closes the comparison gap and lets consumers choose their preferred band without a
schema change. Columns are nullable (consistent with p10/p90) so existing rows and any in-flight
analysis rows remain valid with NULL p5/p95 until the backfill engine recomputes them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_analysis_p5_p95"
down_revision: str | None = "0009_region_clusters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analysis", sa.Column("p5", sa.Float(), nullable=True))
    op.add_column("analysis", sa.Column("p95", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis", "p95")
    op.drop_column("analysis", "p5")
