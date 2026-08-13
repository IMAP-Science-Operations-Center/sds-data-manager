"""Contains all functions needed to calculate spin file dependencies."""

import datetime
import logging
from contextlib import nullcontext
from os.path import basename

from sqlalchemy import and_, desc, func
from sqlalchemy.orm import aliased

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def verify_spin_coverage(
    records: list,
    start_date: datetime,
    end_date: datetime,
) -> bool:
    """Verify that spin files cover the entire date range without gaps.

    Spin files have start_date and end_date ranges. This function verifies:
    1. First record covers or starts before the input start_date
    2. No gaps exist between consecutive record ranges
    3. Last record covers up to or past the input end_date

    If gaps are found, they are logged at INFO level.

    Parameters
    ----------
    records : list
        List of SpinFiles records with file_path, start_date, end_date.
    start_date : datetime
        Expected coverage start date.
    end_date : datetime
        Expected coverage end date.

    Returns
    -------
    bool
        True if coverage is complete, False if gaps exist.
    """
    if not records:
        logger.info(f"No spin files found for {start_date} to {end_date}")
        return False

    # Sort records by start_date
    sorted_records = sorted(records, key=lambda r: r.start_date)

    # Check if first record covers or starts before input start_date
    if sorted_records[0].start_date.replace(
        tzinfo=datetime.timezone.utc
    ) > start_date.replace(tzinfo=datetime.timezone.utc):
        gap_start = start_date
        gap_end = sorted_records[0].start_date - datetime.timedelta(days=1)
        logger.info(
            f"Spin coverage gap at start: Gap from {gap_start.strftime('%Y%m%d')} "
            f"to {gap_end.strftime('%Y%m%d')}"
        )
        return False

    # Check for gaps between consecutive records
    for i in range(len(sorted_records) - 1):
        current_end = sorted_records[i].end_date
        next_start = sorted_records[i + 1].start_date

        # Gap exists if next_start is after current_end
        # (next_start must be on the same day as current_end or overlap)
        if next_start > current_end:
            gap_start = current_end + datetime.timedelta(days=1)
            gap_end = next_start - datetime.timedelta(days=1)
            logger.info(
                f"Spin coverage gap between records: Gap from "
                f"{gap_start.strftime('%Y%m%d')} to {gap_end.strftime('%Y%m%d')}"
            )
            return False

    # Check if last record covers past input end_date
    if sorted_records[-1].end_date.replace(
        tzinfo=datetime.timezone.utc
    ) < end_date.replace(tzinfo=datetime.timezone.utc):
        gap_start = sorted_records[-1].end_date + datetime.timedelta(days=1)
        gap_end = end_date
        logger.info(
            f"Spin coverage gap at end: Gap from {gap_start.strftime('%Y%m%d')} "
            f"to {gap_end.strftime('%Y%m%d')}"
        )
        return False

    logger.info(
        f"Spin coverage verified for {start_date.strftime('%Y%m%d')} to "
        f"{end_date.strftime('%Y%m%d')}: {len(records)} file(s) cover range"
    )
    return True


def get_spin_files(
    session,
    start_date: datetime,
    end_date: datetime,
) -> list:
    """Get spin input.

    Query the spin table for the given date range and get latest version.

    Parameters
    ----------
    session : orm session
        Database session.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.

    Returns
    -------
    list
        List of SpinFiles records with file_path, start_date, end_date, version.
    """
    spin = aliased(models.SpinFiles)

    # Define the row_number() window function
    row_number = (
        func.row_number()
        .over(
            partition_by=(spin.start_date, spin.end_date), order_by=desc(spin.version)
        )
        .label("row_num")
    )

    # Build the subquery with row numbers
    subquery = (
        session.query(
            spin.file_path,
            spin.start_date,
            spin.end_date,
            spin.version,
            row_number,
            spin.ingestion_date,
        )
        .filter(
            and_(
                spin.start_date <= end_date,
                spin.end_date >= start_date,
            )
        )
        .subquery()
    )

    # Outer query to select only latest version per start/end date
    records = (
        session.query(
            subquery.c.file_path,
            subquery.c.start_date,
            subquery.c.end_date,
            subquery.c.version,
        )
        .filter(subquery.c.row_num == 1)
        # Order by ingestion date, newest first
        .order_by(subquery.c.ingestion_date)
        .all()
    )

    return records


def get_upstream_dependency_inputs_spin(
    start_date: datetime,
    end_date: datetime,
    require_coverage: bool = False,
    open_session: db.Session = None,
):
    """Construct a ProcessingInputCollection of dependency files.

    For each dependency, query for existing files in s3 and add any matching files
    found to a ProcessingInputCollection.

    Parameters
    ----------
    dependencies : list
        List of dependency dictionaries either downstream or upstream from the
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.
    repoint : int or list[int], optional
        If provided, will be used to filter files by repoint number(s). Can be a
        single int or a list of ints.
    require_coverage : bool, optional
        If True gathered dependencies will be checked for complete coverage of
        start_date to end_date or repoint coverage.
    open_session : db.Session, optional
        Database session. If not provided, a new session will be created.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """
    # Use provided session or create a new one
    session_context = nullcontext(open_session) if open_session else db.Session()
    with session_context as session:
        spin_records = get_spin_files(session, start_date, end_date)
        if not spin_records:
            logger.info(f"No spin files found for {start_date} to {end_date}")
            return None
        # Verify spin coverage
        if require_coverage and not verify_spin_coverage(
            spin_records, start_date, end_date
        ):
            return None
        spin_files = [basename(record.file_path) for record in spin_records]
        logger.info(f"Found spin files: {spin_files}. Adding to collection.")

    return spin_files
