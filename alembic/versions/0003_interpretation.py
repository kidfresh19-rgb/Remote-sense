"""interpretation: drafted plain-language agronomic reads

Revision ID: 0003_interpretation
Revises: 0002_collection_state
Create Date: 2026-06-01

Phase 4b (L4b). One row per field/pass/geometry-version/prompt-version. Stored unpublished +
needs_review; an agronomist edits and publishes (risk #6, never auto-published). The narrative is
the model's words; status/confidence are grounded in the zonal stats, not the model.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_interpretation"
down_revision: str | None = "0002_collection_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interpretation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", sa.String(length=256), nullable=False),
        sa.Column("pass_date", sa.Date(), nullable=False),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("crop", sa.String(length=64), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["field_id"], ["field.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scene_metadata.scene_id"]),
        sa.UniqueConstraint(
            "field_id",
            "scene_id",
            "geometry_version",
            "prompt_version",
            name="uq_interpretation_identity",
        ),
    )
    op.create_index("ix_interpretation_field_id", "interpretation", ["field_id"])
    op.create_index("ix_interpretation_scene_id", "interpretation", ["scene_id"])
    op.create_index("ix_interpretation_pass_date", "interpretation", ["pass_date"])


def downgrade() -> None:
    op.drop_table("interpretation")
