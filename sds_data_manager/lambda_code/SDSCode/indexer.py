# Standard
import json
import logging
import os
import sys

# Installed
import boto3
from SDSCode.database import models
from SDSCode.database.database import engine
from sqlalchemy.orm import Session

# Local
from .path_helper import FilenameParser

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

s3 = boto3.client("s3")


def lambda_handler(event, context):
    """Handler function for creating metadata, adding it to the payload,
    and sending it to the opensearch instance.

    This function is an event handler called by the AWS Lambda upon the creation of an
    object in a s3 bucket.

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
            "start_date": filename_parsed.startdate,
            "end_date": filename_parsed.enddate,
            "version": filename_parsed.version,
            "extension": filename_parsed.extension,
        }

        # Add data to the file catalog
        with Session(engine) as session:
            session.add(models.FileCatalog(**metadata_params))
            session.commit()
