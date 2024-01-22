import datetime
import json
import logging
import os
import sys

import boto3
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .database import database as db
from .database import models
from .path_helper import FilenameParser

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

s3 = boto3.client("s3")


def lambda_handler(event, context):
    """Handler function for creating metadata, adding it to the database.

    This function is an event handler for multiple event sources.
    List of event sources are aws.s3, aws.batch and imap.lambda.
    imap.lambda is custom PutEvent from AWS lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.
    """
    logger.info("Received event: " + json.dumps(event, indent=2))

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")
    engine = db.get_engine()

    # We're only expecting one record, but for some reason the Records are a list object
    # TODO: events no longer have a Records key with list. This is already planned for
    # removal in an upcoming PR.
    for record in event["Records"]:
        # Retrieve the Object name
        logger.info(f"Record Received: {record}")
        filename = record["s3"]["object"]["key"]

        logger.info(f"Attempting to insert {os.path.basename(filename)} into database")
        filename_parsed = FilenameParser(os.path.basename(filename))
        filepath = filename_parsed.upload_filepath()

        # confirm that the file is valid
        if filepath["statusCode"] != 200:
            logger.error(filepath["body"])
            break

        # setup a dictionary of metadata parameters to unpack in the
        # instrument table
        metadata_params = {
            "file_path": filepath["body"],
            "instrument": filename_parsed.instrument,
            "data_level": filename_parsed.data_level,
            "descriptor": filename_parsed.descriptor,
            "start_date": datetime.datetime.strptime(
                filename_parsed.startdate, "%Y%m%d"
            ),
            "end_date": datetime.datetime.strptime(filename_parsed.enddate, "%Y%m%d"),
            "version": filename_parsed.version,
            "extension": filename_parsed.extension,
        }

        # Add data to the file catalog
        with Session(engine) as session:
            session.add(models.FileCatalog(**metadata_params))
            session.commit()

            # TODO: These are sanity check. will remove
            # from upcoming PR
            result = session.query(models.FileCatalog).all()
            for row in result:
                print(row.instrument)
                print(row.file_path)

            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            print(table_names)
