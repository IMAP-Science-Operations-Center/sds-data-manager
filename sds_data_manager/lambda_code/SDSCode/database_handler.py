"""Common functions to write to database"""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import database as db
from .database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def update_status_table(status_params):
    """Update status tracking table

    Parameters
    ----------
    status_params : dict
        Data information
    """
    try:
        # Add data to the file catalog and status tables
        with Session(db.get_engine()) as session:
            # Add data to the status tracking table
            session.add(models.StatusTracking(**status_params))
            session.commit()
    except IntegrityError as e:
        logger.error(str(e))


def update_file_catalog_table(metadata_params):
    """Update file catalog table

    Parameters
    ----------
    metadata_params : dict
        Data information
    """
    try:
        # Add data to the file catalog
        with Session(db.get_engine()) as session:
            # Add data to the file catalog table
            session.add(models.FileCatalog(**metadata_params))
            session.commit()
    except IntegrityError as e:
        logger.error(str(e))
