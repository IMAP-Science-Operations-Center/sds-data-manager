"""major_minor_versions.

Revision ID: e5dee361947f
Revises: f110e214a9cc
Create Date: 2026-06-16 12:28:10.431421

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5dee361947f'
down_revision: Union[str, Sequence[str], None] = 'f110e214a9cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: transition to major.minor versioning."""

    # Tables that currently have version as VARCHAR (e.g., "v001")
    TABLES_WITH_VERSION_STRING = ("idex_l0_files", "science_files", "quicklook_files", "processing_job_table")

    # Process tables with string version
    for table in TABLES_WITH_VERSION_STRING:
        # 1. Add new columns as nullable first
        op.add_column(table, sa.Column('major_version', sa.Integer(), nullable=True))
        op.add_column(table, sa.Column('minor_version', sa.Integer(), nullable=True))

        # 2. Convert version "v001" → 1 and populate minor_version
        op.execute(
            sa.text(f"""
                UPDATE {table}
                SET minor_version = CAST(REPLACE(version, 'v', '') AS INTEGER),
                    major_version = 1
            """)
        )

        # 3. Make columns non-nullable
        op.alter_column(table, 'major_version', nullable=False)
        op.alter_column(table, 'minor_version', nullable=False)

        # 4. Drop old version column
        op.drop_column(table, 'version')

        # 5. Add check constraints
        op.create_check_constraint(
            "ck_major_version_max_999",
            table,
            "major_version >= 0 AND major_version <= 999",
        )
        op.create_check_constraint(
            "ck_minor_version_max_9999",
            table,
            "minor_version >= 0 AND minor_version <= 9999",
        )


def downgrade() -> None:
    """Downgrade schema: revert to previous versioning scheme."""

    # Tables that need to revert to version VARCHAR
    TABLES_WITH_VERSION_STRING = ("idex_l0_files", "science_files", "quicklook_files", "processing_job_table")

    # Process tables that should have string version
    for table in TABLES_WITH_VERSION_STRING:
        # 1. Drop check constraints first
        op.drop_constraint("ck_major_version_max_999", table, type_="check")
        op.drop_constraint("ck_minor_version_max_9999", table, type_="check")

        # 2. Add back version column as VARCHAR
        op.add_column(table, sa.Column('version', sa.VARCHAR(length=4), nullable=True))

        # 3. Populate version from minor_version (convert 1 → "v001")
        op.execute(
            sa.text(f"""
                UPDATE {table}
                SET version = 'v' || LPAD(minor_version::text, 3, '0')
            """)
        )

        # 4. Make version non-nullable
        op.alter_column(table, 'version', nullable=False)

        # 5. Drop new columns
        op.drop_column(table, 'minor_version')
        op.drop_column(table, 'major_version')
