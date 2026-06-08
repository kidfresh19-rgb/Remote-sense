"""add agritrack_farmer_id to farm

Revision ID: 0006_agritrack_farmer_id
Revises: 0005_annotation
Create Date: 2026-06-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_agritrack_farmer_id"
down_revision: str | None = "0005_annotation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("farm", sa.Column("agritrack_farmer_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("farm", "agritrack_farmer_id")
