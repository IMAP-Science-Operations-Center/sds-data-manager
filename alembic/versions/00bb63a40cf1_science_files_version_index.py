"""science_files_version_index.

Revision ID: 00bb63a40cf1
Revises: e5dee361947f
Create Date: 2026-07-15 18:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00bb63a40cf1"
down_revision: str | Sequence[str] | None = "e5dee361947f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Speeds up the correlated "latest version" subqueries in query_api.py, which
# scan for MAX(major_version)/MAX(minor_version) grouped by these columns.
# See GitHub issue #1256.
_VERSION_INDEX_COLUMNS = [
    "instrument",
    "data_level",
    "descriptor",
    "start_date",
    "repointing",
    "major_version",
    "minor_version",
]
_TABLES = ("science_files", "quicklook_files")


def upgrade() -> None:
    """Upgrade schema: add composite version index to science/quicklook files."""
    for table in _TABLES:
        op.create_index(
            f"idx_{table}_version",
            table,
            _VERSION_INDEX_COLUMNS,
        )


def downgrade() -> None:
    """Downgrade schema: drop composite version index."""
    for table in _TABLES:
        op.drop_index(f"idx_{table}_version", table_name=table)
