"""add_repointing_to_ancillary_files.

Revision ID: a30ad4dde7e8
Revises: 00bb63a40cf1
Create Date: 2026-09-08 15:44:29.707833

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a30ad4dde7e8"
down_revision: str | Sequence[str] | None = "00bb63a40cf1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    """Upgrade schema."""
    # Both tables inherit AncillaryFileBase and share its ingestion path.
    op.add_column(
        "ancillary_files", sa.Column("repointing", sa.Integer(), nullable=True)
    )
    op.add_column(
        "release_files", sa.Column("repointing", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("ancillary_files", "repointing")
    op.drop_column("release_files", "repointing")
