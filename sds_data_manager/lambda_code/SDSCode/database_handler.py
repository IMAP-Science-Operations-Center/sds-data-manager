"""Common functions to write to database."""

import logging

from sqlalchemy.exc import IntegrityError

from .database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
