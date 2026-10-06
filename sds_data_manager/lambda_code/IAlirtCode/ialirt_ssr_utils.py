"""Utilities for processing I-ALiRT SSR data."""

import logging
from datetime import datetime

import imap_data_access

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def query_filenames(day: datetime) -> str | None:
    """Query the latest I-ALiRT L0 file for a given day.

    Parameters
    ----------
    day : datetime
        The day to query.

    Returns
    -------
    filename : str or None
        File path relative to the data archive root, or None if no file
        exists for the day.
    """
    date_str = day.strftime("%Y%m%d")
    results = imap_data_access.query(
        table="science",
        instrument="ialirt",
        data_level="l0",
        descriptor="raw",
        start_date=date_str,
        end_date=date_str,
        version="latest",
    )

    if not results:
        logger.warning(f"No I-ALiRT L0 file found for {date_str}")
        return None

    filename = results[0]["file_path"]
    logger.info(f"Found I-ALiRT L0 file for {date_str}: {filename}")

    return filename
