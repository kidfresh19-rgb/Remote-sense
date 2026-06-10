"""add_grounding_context_to_interpretation

Revision ID: 44959eed8b1a
Revises: 0007_heal_agritrack_subplots
Create Date: 2026-06-09 09:13:08.350592

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '44959eed8b1a'
down_revision: str | None = '0007_heal_agritrack_subplots'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('interpretation', sa.Column('gdd_accumulation', sa.Float(), nullable=True))
    op.add_column('interpretation', sa.Column('total_precipitation', sa.Float(), nullable=True))
    op.add_column('interpretation', sa.Column('recent_activities', postgresql.JSONB(astext_type=sa.Text()), nullable=True))  # noqa: E501


def downgrade() -> None:
    op.drop_column('interpretation', 'recent_activities')
    op.drop_column('interpretation', 'total_precipitation')
    op.drop_column('interpretation', 'gdd_accumulation')
