"""annotation: shared, team-visible field notes

Revision ID: 0005_annotation
Revises: 0004_sync_outbox
Create Date: 2026-06-03

The server-backed replacement for the former browser-local annotation store. Written behind the
RBAC `annotate` permission, read behind `view`. Pinned to a geometry_version (invariant 5);
`author` is the verified token subject. Append-and-delete, no identity constraint (many notes may
pin to the same field/pass). Cascades with its field.
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
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("pass_date", sa.Date(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["field_id"], ["field.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_annotation_field_id", "annotation", ["field_id"])


def downgrade() -> None:
    op.drop_table("annotation")
