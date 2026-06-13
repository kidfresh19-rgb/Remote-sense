"""partition the analysis table by month on pass_date (S4.1)

Revision ID: 0008_partition_analysis
Revises: 44959eed8b1a
Create Date: 2026-06-12

PRD 0001 R15/R18: scale is data volume - hundreds of thousands of farms accruing ~6 passes a
month across several indices, rows never deleted - so `analysis` becomes RANGE-partitioned by
month on `pass_date`. Postgres requires the partition key inside the primary key and every
unique constraint, so the key widens to (id, pass_date) and `uq_analysis_identity` gains
`pass_date` as its trailing column. The scientific identity is still
(field, scene, index, geometry_version, formula_version): `pass_date` is derived from the
scene's immutable sensing_datetime, so one scene has exactly one date and the wider constraint
admits no duplicate the old one rejected. Nothing references analysis.id from another table, so
the widened key breaks no FK; the upsert keeps conflicting on the same constraint name.

Indexing: the four single-column indexes collapse into the workhorse composite
(field_id, index_name, pass_date) - every read path leads with field_id (timeseries, as-of,
audit, farm joins through field) or bounds pass_date (served by partition pruning). field_id
alone is this index's prefix, index_name never stands alone, and scene_id keeps its FK but
loses its index: no read path filters by scene alone and scene_metadata rows are never deleted.

Partition layout: an `analysis_default` catch-all plus monthly partitions covering every stored
pass and the rolling write window (one slack month behind the backfill horizon, three months
ahead). The weekly `maintenance.ensure_analysis_partitions` task keeps the window rolling after
this migration runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_partition_analysis"
down_revision: str | None = "44959eed8b1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors services.worker.partitions (slack + lookahead) and the default backfill depth + slack
# (fallback when the table is empty). Inlined: a migration must stay frozen even if app code moves.
_FALLBACK_MONTHS_BEHIND = 19
_LOOKAHEAD_MONTHS = 3

# One definition for both directions of the copy; identical order on both sides of the INSERT.
_COLUMNS = (
    "id, field_id, scene_id, pass_date, index_name, mean, min_val, max_val, std, p10, p90, "
    "clear_fraction, resolution_m, formula_version, geometry_version, provider, "
    "provider_scene_id, processing_mode, cog_uri, confidence, created_at"
)


def _month_floor(day: date) -> date:
    return day.replace(day=1)


def _add_months(month_start: date, months: int) -> date:
    total = month_start.year * 12 + (month_start.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _column_defs() -> list[sa.Column]:
    """The analysis columns, identical to revision 0001 - only the keys around them change."""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", sa.String(length=256), nullable=False),
        sa.Column("pass_date", sa.Date(), nullable=False),
        sa.Column("index_name", sa.String(length=32), nullable=False),
        sa.Column("mean", sa.Float(), nullable=True),
        sa.Column("min_val", sa.Float(), nullable=True),
        sa.Column("max_val", sa.Float(), nullable=True),
        sa.Column("std", sa.Float(), nullable=True),
        sa.Column("p10", sa.Float(), nullable=True),
        sa.Column("p90", sa.Float(), nullable=True),
        sa.Column("clear_fraction", sa.Float(), nullable=False),
        sa.Column("resolution_m", sa.Float(), nullable=False),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("geometry_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_scene_id", sa.String(length=256), nullable=False),
        sa.Column("processing_mode", sa.String(length=32), nullable=False),
        sa.Column("cog_uri", sa.String(length=512), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # The month window to pre-create: every stored pass plus the rolling write window the weekly
    # maintenance task maintains from here on. Computed before the indexes go away.
    stored_min, stored_max = bind.execute(
        sa.text("SELECT min(pass_date), max(pass_date) FROM analysis")
    ).one()
    today = date.today()
    first = _add_months(_month_floor(today), -_FALLBACK_MONTHS_BEHIND)
    last = _add_months(_month_floor(today), _LOOKAHEAD_MONTHS)
    if stored_min is not None:
        first = min(first, _month_floor(stored_min))
    if stored_max is not None:
        last = max(last, _month_floor(stored_max))

    # Move the flat table aside and shed its keys, freeing every name the partitioned successor
    # reuses. Index names are schema-global; FK names are per-table but still dropped + recreated
    # explicitly, because Postgres's auto-namer avoids any name present in the namespace and
    # would mint analysis_field_id_fkey1 while the old table lingers.
    op.execute("ALTER TABLE analysis RENAME TO analysis_flat")
    op.execute("ALTER TABLE analysis_flat DROP CONSTRAINT uq_analysis_identity")
    op.execute("ALTER TABLE analysis_flat DROP CONSTRAINT analysis_pkey")
    op.execute("ALTER TABLE analysis_flat DROP CONSTRAINT analysis_field_id_fkey")
    op.execute("ALTER TABLE analysis_flat DROP CONSTRAINT analysis_scene_id_fkey")
    op.drop_index("ix_analysis_field_id", table_name="analysis_flat")
    op.drop_index("ix_analysis_scene_id", table_name="analysis_flat")
    op.drop_index("ix_analysis_pass_date", table_name="analysis_flat")
    op.drop_index("ix_analysis_index_name", table_name="analysis_flat")

    op.create_table(
        "analysis",
        *_column_defs(),
        sa.PrimaryKeyConstraint("id", "pass_date"),
        sa.ForeignKeyConstraint(
            ["field_id"], ["field.id"], ondelete="CASCADE", name="analysis_field_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"], ["scene_metadata.scene_id"], name="analysis_scene_id_fkey"
        ),
        sa.UniqueConstraint(
            "field_id",
            "scene_id",
            "index_name",
            "geometry_version",
            "formula_version",
            "pass_date",
            name="uq_analysis_identity",
        ),
        postgresql_partition_by="RANGE (pass_date)",
    )

    op.execute("CREATE TABLE analysis_default PARTITION OF analysis DEFAULT")
    month = first
    while month <= last:
        op.execute(
            f"CREATE TABLE analysis_y{month.year:04d}m{month.month:02d} "
            f"PARTITION OF analysis "
            f"FOR VALUES FROM ('{month.isoformat()}') TO ('{_add_months(month, 1).isoformat()}')"
        )
        month = _add_months(month, 1)

    op.execute(f"INSERT INTO analysis ({_COLUMNS}) SELECT {_COLUMNS} FROM analysis_flat")
    op.drop_table("analysis_flat")

    # After the copy, so the build is one pass instead of per-row maintenance.
    op.create_index(
        "ix_analysis_field_index_date", "analysis", ["field_id", "index_name", "pass_date"]
    )


def downgrade() -> None:
    # Rebuild the flat table under temporary names, copy back, drop the partitioned tree (its
    # partitions go with it), then take over the original names. Rows can never collapse onto one
    # flat identity: pass_date is functionally dependent on scene_id, which the identity keeps.
    op.create_table(
        "analysis_flat",
        *_column_defs(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["field_id"], ["field.id"], ondelete="CASCADE", name="analysis_flat_field_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"], ["scene_metadata.scene_id"], name="analysis_flat_scene_id_fkey"
        ),
        sa.UniqueConstraint(
            "field_id",
            "scene_id",
            "index_name",
            "geometry_version",
            "formula_version",
            name="uq_analysis_identity_flat",
        ),
    )
    op.execute(f"INSERT INTO analysis_flat ({_COLUMNS}) SELECT {_COLUMNS} FROM analysis")
    op.drop_table("analysis")
    op.execute("ALTER TABLE analysis_flat RENAME TO analysis")
    op.execute(
        "ALTER TABLE analysis RENAME CONSTRAINT uq_analysis_identity_flat TO uq_analysis_identity"
    )
    op.execute(
        "ALTER TABLE analysis RENAME CONSTRAINT analysis_flat_field_id_fkey "
        "TO analysis_field_id_fkey"
    )
    op.execute(
        "ALTER TABLE analysis RENAME CONSTRAINT analysis_flat_scene_id_fkey "
        "TO analysis_scene_id_fkey"
    )
    op.execute("ALTER INDEX analysis_flat_pkey RENAME TO analysis_pkey")
    op.create_index("ix_analysis_field_id", "analysis", ["field_id"])
    op.create_index("ix_analysis_scene_id", "analysis", ["scene_id"])
    op.create_index("ix_analysis_pass_date", "analysis", ["pass_date"])
    op.create_index("ix_analysis_index_name", "analysis", ["index_name"])
