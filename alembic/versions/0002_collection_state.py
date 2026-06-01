"""collection state: per-field per-geometry-version pipeline cursor

Revision ID: 0002_collection_state
Revises: 0001_initial_schema
Create Date: 2026-05-31

Phase 3 (D5). Tracks backfill completion plus the forward-fill cursor (last poll, sensing-date
watermark) per (field_id, geometry_version), so collection is resumable and a boundary change
(DI-5) gets a fresh cursor. No geometry here; it references field.id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_collection_state"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_collection_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("backfill_complete", sa.Boolean(), nullable=False),
        sa.Column("backfill_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_date", sa.Date(), nullable=True),
        sa.Column("last_scene_id", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["field_id"], ["field.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("field_id", "geometry_version", name="uq_collection_state_field_geom"),
    )
    op.create_index("ix_field_collection_state_field_id", "field_collection_state", ["field_id"])


def downgrade() -> None:
    op.drop_table("field_collection_state")
