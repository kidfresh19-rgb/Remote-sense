"""annotation: shared analyst field notes

Revision ID: 0005_annotation
Revises: 0004_sync_outbox
Create Date: 2026-06-02

Phase 5/L6. A team-visible store for analyst notes on a field, replacing the browser-local seam.
Each note is tied to the geometry_version it was written against (invariant 5) and optionally to a
pass date. Writes go behind the RBAC `annotate` permission.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_annotation"
down_revision: str | None = "0004_sync_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "annotation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "field_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("pass_date", sa.Date(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_annotation_field_id", "annotation", ["field_id"])


def downgrade() -> None:
    op.drop_table("annotation")
