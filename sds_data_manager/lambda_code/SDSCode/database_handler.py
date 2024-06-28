"""Common functions to write to database."""

import logging

from sqlalchemy.exc import IntegrityError

from .database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def update_status_table(session, status_params):
    """Update status tracking table.

    Parameters
    ----------
    session : sqlalchemy.orm.session.Session
        Database session.
    status_params : dict
        Data information

    """
    instrument = status_params["instrument"]
    data_level = status_params["data_level"]
    descriptor = status_params["descriptor"]
    start_date = status_params["start_date"]
    version = status_params["version"]

    try:
        # Had to query this way because the select statement
        # returns a RowProxy object when it executes it,
        # not the actual StatusTracking model instance,
        # which is why it can't update table row directly.
        result = (
            session.query(models.StatusTracking)
            .filter(models.StatusTracking.instrument == instrument)
            .filter(models.StatusTracking.data_level == data_level)
            .filter(models.StatusTracking.descriptor == descriptor)
            .filter(models.StatusTracking.start_date == start_date)
            .filter(models.StatusTracking.version == version)
            .first()
        )

        if result is None:
            logger.info(
                "No existing record found, creating"
                f" new record for {instrument}, {data_level},"
                f"{descriptor}, {start_date}, {version}"
            )
            session.add(models.StatusTracking(**status_params))
            session.commit()
        else:
            logger.info(f"Updating existing record, {result.__dict__}, with batch info")
            result.status = status_params["status"]
            result.job_definition = status_params["job_definition"]
            result.job_log_stream_id = status_params["job_log_stream_id"]
            result.container_image = status_params["container_image"]
            result.container_command = status_params["container_command"]
            session.commit()

    except IntegrityError as e:
        logger.error(str(e))


def update_file_catalog_table(session, metadata_params):
    """Update file catalog table.

    Parameters
    ----------
    session : sqlalchemy.orm.session.Session
        Database session.
    metadata_params : dict
        Data information

    """
    try:
        # Add data to the file catalog table
        session.add(models.FileCatalog(**metadata_params))
        session.commit()
    except IntegrityError as e:
        logger.error(str(e))
