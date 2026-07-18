"""Science-file "latest version" resolution logic for the query API."""

from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, func, select

from ..database.models import FILE_ID_COLUMNS, ScienceFiles


def build_latest_version_query(
    filters: Sequence[ColumnElement] = (),
    major_only: bool = False,
) -> Select:
    """Build a query selecting the latest version of each science file.

    Rows that share every :data:`FILE_ID_COLUMNS` value are just different
    versions of the same file. The query uses a window function to rank them
    by version and keeps only the ones with rank=1.

    Parameters
    ----------
    filters : sequence of column expressions, optional
        WHERE conditions applied *before* the version selection.
    major_only : bool, optional
        When True, return every minor version of each series' latest major
        version instead of just the single newest file.

    Returns
    -------
    Select
        A SELECT of the :class:`ScienceFiles` table columns, restricted to the
        latest version of each series.

    """
    table = ScienceFiles.__table__

    partition_by = [table.c[column] for column in FILE_ID_COLUMNS]
    order_by = [table.c.major_version.desc()]
    if not major_only:
        order_by.append(table.c.minor_version.desc())
    rank = (
        func.rank()
        .over(partition_by=partition_by, order_by=order_by)
        .label("version_rank")
    )
    ranked = select(table, rank).where(*filters).subquery()

    rank_col = ranked.c[rank.name]
    top_only = select(ranked).where(rank_col == 1)

    # excludes the added RANK column
    original_columns = [ranked.c[col.name] for col in table.c]
    return top_only.with_only_columns(*original_columns)
