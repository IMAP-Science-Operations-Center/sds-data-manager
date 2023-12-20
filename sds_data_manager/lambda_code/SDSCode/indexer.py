# Standard
import json
import logging
import os
import sys

# Installed
import boto3

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
    for record in event["Records"]:
        # Retrieve the Object name
        logger.info(f"Record Received: {record}")
        filename = record["s3"]["object"]["key"]

        logger.info(f"Attempting to insert {os.path.basename(filename)} into database")
        # TODO: change below logics to use new FilenameParser
        # when we create schema and write file metadata to DB
        filename_parsed = FilenameParser(filename)
        filename_parsed.upload_filepath()
        metadata = None

        # TODO: remove this check since upload api validates filename?
        # Found nothing. This should probably send out an error notification
        # to the team, because how did it make its way onto the SDS?
        if metadata is None:
            logger.info("Found no matching file types to index this file against.")
            return None

        logger.info("Found the following metadata to index: " + str(metadata))
