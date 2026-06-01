"""sync_outbox: outbound gateway push ledger

Revision ID: 0004_sync_outbox
Revises: 0003_interpretation
Create Date: 2026-06-01

Phase 6 (L7). One row per push, keyed by its idempotency key (unique), recording published vs
dead-letter so a re-publish is a DB-level no-op (R-2) and failures are retained for retry. No
geometry - only the canonical farm id and push provenance.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_sync_outbox"
down_revision: str | None = "0003_interpretation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("canonical_farm_id", sa.String(length=128), nullable=False),
        sa.Column("payload_version", sa.String(length=32), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_sync_outbox_idempotency_key"),
    )
    op.create_index("ix_sync_outbox_idempotency_key", "sync_outbox", ["idempotency_key"])
    op.create_index("ix_sync_outbox_canonical_farm_id", "sync_outbox", ["canonical_farm_id"])


def downgrade() -> None:
    op.drop_table("sync_outbox")
