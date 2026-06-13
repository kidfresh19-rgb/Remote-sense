"""heal agritrack subplots

Revision ID: 0007_heal_agritrack_subplots
Revises: 0006_agritrack_farmer_id
Create Date: 2026-06-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_heal_agritrack_subplots"
down_revision: str | None = "0006_agritrack_farmer_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Find all fields that have a duplicated prefix (e.g. 9.9.21)
    result = conn.execute(
        sa.text(
            "SELECT id, farm_id, canonical_field_id FROM field WHERE canonical_field_id LIKE '%.%'"
        )
    )
    fields = result.fetchall()

    for field_id, farm_id, canonical_id in fields:
        parts = canonical_id.split(".")
        if len(parts) >= 3 and parts[0] == parts[1]:
            corrected_id = f"{parts[0]}.{'.'.join(parts[2:])}"

            # Check if the corrected field already exists for this farm
            res_exists = conn.execute(
                sa.text(
                    "SELECT id FROM field WHERE farm_id = :farm_id "
                    "AND canonical_field_id = :canonical_id"
                ),
                {"farm_id": farm_id, "canonical_id": corrected_id},
            )
            exists_row = res_exists.fetchone()

            if exists_row:
                target_field_id = exists_row[0]

                # Move analyses (delete duplicates if they would cause constraint violations)
                conn.execute(
                    sa.text("""
                        DELETE FROM analysis a1
                        WHERE a1.field_id = :dup_id
                        AND EXISTS (
                            SELECT 1 FROM analysis a2
                            WHERE a2.field_id = :target_id
                            AND a2.scene_id = a1.scene_id
                            AND a2.index_name = a1.index_name
                            AND a2.geometry_version = a1.geometry_version
                            AND a2.formula_version = a1.formula_version
                        )
                    """),
                    {"dup_id": field_id, "target_id": target_field_id},
                )
                conn.execute(
                    sa.text("UPDATE analysis SET field_id = :target_id WHERE field_id = :dup_id"),
                    {"dup_id": field_id, "target_id": target_field_id},
                )

                # Move interpretations
                conn.execute(
                    sa.text("""
                        DELETE FROM interpretation i1
                        WHERE i1.field_id = :dup_id
                        AND EXISTS (
                            SELECT 1 FROM interpretation i2
                            WHERE i2.field_id = :target_id
                            AND i2.scene_id = i1.scene_id
                            AND i2.geometry_version = i1.geometry_version
                            AND i2.prompt_version = i1.prompt_version
                        )
                    """),
                    {"dup_id": field_id, "target_id": target_field_id},
                )
                conn.execute(
                    sa.text(
                        "UPDATE interpretation SET field_id = :target_id WHERE field_id = :dup_id"
                    ),
                    {"dup_id": field_id, "target_id": target_field_id},
                )

                # Move annotations
                conn.execute(
                    sa.text("UPDATE annotation SET field_id = :target_id WHERE field_id = :dup_id"),
                    {"dup_id": field_id, "target_id": target_field_id},
                )

                # Move field_collection_state
                conn.execute(
                    sa.text("""
                        DELETE FROM field_collection_state fcs1
                        WHERE fcs1.field_id = :dup_id
                        AND EXISTS (
                            SELECT 1 FROM field_collection_state fcs2
                            WHERE fcs2.field_id = :target_id
                            AND fcs2.geometry_version = fcs1.geometry_version
                        )
                    """),
                    {"dup_id": field_id, "target_id": target_field_id},
                )
                conn.execute(
                    sa.text(
                        "UPDATE field_collection_state SET field_id = :target_id "
                        "WHERE field_id = :dup_id"
                    ),
                    {"dup_id": field_id, "target_id": target_field_id},
                )

                # Move field_geometry_version
                conn.execute(
                    sa.text("""
                        DELETE FROM field_geometry_version fgv1
                        WHERE fgv1.field_id = :dup_id
                        AND EXISTS (
                            SELECT 1 FROM field_geometry_version fgv2
                            WHERE fgv2.field_id = :target_id
                            AND fgv2.version = fgv1.version
                        )
                    """),
                    {"dup_id": field_id, "target_id": target_field_id},
                )
                conn.execute(
                    sa.text(
                        "UPDATE field_geometry_version SET field_id = :target_id "
                        "WHERE field_id = :dup_id"
                    ),
                    {"dup_id": field_id, "target_id": target_field_id},
                )

                # Delete duplicate field
                conn.execute(sa.text("DELETE FROM field WHERE id = :dup_id"), {"dup_id": field_id})
            else:
                # Rename the field
                conn.execute(
                    sa.text(
                        "UPDATE field SET canonical_field_id = :corrected_id WHERE id = :dup_id"
                    ),
                    {"dup_id": field_id, "corrected_id": corrected_id},
                )


def downgrade() -> None:
    # Downgrade is a no-op since this is a data healing migration.
    pass
