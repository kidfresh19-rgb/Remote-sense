"""ward watch field-diagnosis flywheel

Revision ID: 0013_ward_watch_diagnosis
Revises: 0012_ward_watch_plot_analysis
Create Date: 2026-06-30

The officer field-diagnosis capture table (PRD 0003 §0, §12.9, backlog 0038): one labelled
ground-truth point per plot (observed crop -> condition -> cause -> recommended action), with
provenance tying the label to the scene observed against (invariant 5 / §1.5). The controlled-vocab
fields are validated in code (`rs_core.diagnosis`, `rs_core.crops`), never free text; `notes` is the
only free text. Shaped for later export as a training-label set - the flywheel that makes real crop
classification and yield calibration feasible over two to three seasons.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_ward_watch_diagnosis"
down_revision: str | None = "0012_ward_watch_plot_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnosis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", sa.String(length=256), nullable=True),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("observed_crop", sa.String(length=64), nullable=False),
        sa.Column("condition", sa.String(length=32), nullable=False),
        sa.Column("cause", sa.String(length=32), nullable=False),
        sa.Column("recommended_action", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("officer_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["plot_id"], ["plot.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_diagnosis_plot_id", "diagnosis", ["plot_id"])
    op.create_index("ix_diagnosis_officer_id", "diagnosis", ["officer_id"])


def downgrade() -> None:
    op.drop_index("ix_diagnosis_officer_id", table_name="diagnosis")
    op.drop_index("ix_diagnosis_plot_id", table_name="diagnosis")
    op.drop_table("diagnosis")
