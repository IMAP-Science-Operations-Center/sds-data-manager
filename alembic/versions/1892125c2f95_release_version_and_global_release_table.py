"""description.

Revision ID: 1892125c2f95
Revises: f110e214a9cc
Create Date: 2026-06-04 11:24:32.751791

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1892125c2f95"
down_revision: str | Sequence[str] | None = "f110e214a9cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ("science_files", "quicklook_files", "processing_job_table")


def upgrade() -> None:
    """Upgrade schema: convert version string → int + add constraints."""
    # First, add global_release table
    op.create_table(
        "global_release",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(always=False, start=1, increment=1),
            nullable=False,
        ),
        sa.Column("release_number", sa.Integer(), nullable=False),
        sa.Column("updated_date", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("release_number"),
    )

    # Steps for science and quicklook tables:
    for table in TABLES:
        # 1. Rename version to data_version
        op.alter_column(
            table,
            "version",
            new_column_name="data_version",
            existing_type=sa.String(length=4),
        )

        # 2. Convert data_version from "v001" → 1
        op.execute(
            sa.text(f"""
                UPDATE {table}
                SET data_version =
                    CAST(REPLACE(data_version, 'v', '') AS INTEGER)
            """)
        )

        # 3. Alter column type String(4) → Integer
        op.alter_column(
            table,
            "data_version",
            type_=sa.Integer(),
            existing_type=sa.String(length=4),
            postgresql_using="data_version::integer",
        )

        # 4. Add release_number column with default is 0
        op.add_column(
            table,
            sa.Column(
                "release_number", sa.Integer(), nullable=False, server_default="0"
            ),
        )


        # 5. Add constraints for data_version and release_number column
        op.create_check_constraint(
            "ck_data_version_max_999",
            table,
            "data_version >= 0 AND data_version <= 999",
        )

        op.create_check_constraint(
            "ck_release_number_max_999",
            table,
            "release_number >= 0 AND release_number <= 999",
        )


def downgrade() -> None:
    """Downgrade schema: int → version string safely."""
    for table in TABLES:
        # 1. Drop constraints first
        op.drop_constraint("ck_data_version_max_999", table, type_="check")
        op.drop_constraint("ck_release_number_max_999", table, type_="check")

        # 2. FIRST change type INTEGER → TEXT (this is required!)
        op.alter_column(
            table,
            "data_version",
            type_=sa.String(length=4),
            existing_type=sa.Integer(),
            postgresql_using="data_version::text",
        )

        # 3. Now safely change column type INTEGER → VARCHAR(4)
        op.alter_column(
            table,
            "data_version",
            type_=sa.String(length=4),
            existing_type=sa.Integer(),
            postgresql_using="('v' || LPAD(data_version::text, 3, '0'))",
        )

        # 4. Rename back
        op.alter_column(
            table,
            "data_version",
            new_column_name="version",
            existing_type=sa.String(length=4),
        )

        # 5. Drop column
        op.drop_column(table, "release_number")

    op.drop_table("global_release")
