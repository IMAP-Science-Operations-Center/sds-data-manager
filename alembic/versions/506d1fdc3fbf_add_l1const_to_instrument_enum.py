"""add_l1const_to_instrument_enum.

Revision ID: 506d1fdc3fbf
Revises: a30ad4dde7e8
Create Date: 2026-10-21

"""

from collections.abc import Sequence

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "506d1fdc3fbf"
down_revision: str | Sequence[str] | None = "a30ad4dde7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # "l1const" was added to imap_data_access.VALID_INSTRUMENTS, and
    # models.py builds the Postgres "instrument" enum from that list,
    # but Postgres does not add new values to an existing enum type
    # automatically — existing databases need this ALTER TYPE or the
    # indexer fails to insert l1const files and they cannot be queried.
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument ADD VALUE IF NOT EXISTS 'l1const'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres does not support removing a value from an enum type,
    # so there is no downgrade for this migration.
    force = context.get_x_argument(
        as_dictionary=True
    ).get("allow_no_op_downgrades") == "true"

    if not force:
        raise RuntimeError(
            "Cannot downgrade: PostgreSQL does not support removing enum values. "
            "Use '-x allow_no_op_downgrades=true' to acknowledge this limitation."
        )
