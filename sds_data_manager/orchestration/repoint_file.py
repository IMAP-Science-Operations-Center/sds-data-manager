"""Contains all functions needed to calculate repoint file dependencies."""

import datetime
import logging
from contextlib import nullcontext
from os.path import basename

from sqlalchemy import desc

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_latest_repoint_file(
    end_date: datetime,
    session: db.Session = None,
) -> str | None:
    """Get latest repoint file.

    Query for the latest repoint file for given end_date.

    Parameters
    ----------
    end_date : datetime
        End date to find dependent files with.
    session : db.Session, optional
        Database session. If not provided, a new session will be created.

    Returns
    -------
    str
        Latest repoint file name.
    """

    def _query_latest_repoint(sess):
        return (
            sess.query(models.RepointFiles)
            .order_by(desc(models.RepointFiles.file_path))
            .first()
        )

    if session is not None:
        latest_repoint_file = _query_latest_repoint(session)
    else:
        with db.Session() as new_session:
            latest_repoint_file = _query_latest_repoint(new_session)

    if not latest_repoint_file:
        raise ValueError("No Repoint file found in the database.")

    if latest_repoint_file.end_date.replace(
        tzinfo=datetime.timezone.utc
    ) < end_date.replace(tzinfo=datetime.timezone.utc):
        logger.info(
            f"Latest repoint file end date {latest_repoint_file.end_date} "
            f"is before input end date {end_date}"
        )
        return None

    return basename(latest_repoint_file.file_path)


def get_upstream_dependency_inputs_repoint(
    start_date: datetime,
    end_date: datetime,
    open_session: db.Session = None,
):
    """Construct a ProcessingInputCollection of dependency files.

    Parameters
    ----------
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.
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
        latest_repoint_file = get_latest_repoint_file(end_date, session)
        if latest_repoint_file is None:
            logger.info(f"No repoint file found for {start_date} to {end_date}")
            return None
        logger.info(f"Found repoint file: {latest_repoint_file}.")

    return [latest_repoint_file]
