"""Lambda runtime code that triggers off of arrival of data into S3 bucket.
"""
import logging
import os
from datetime import datetime

import boto3

# Setup the logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TODO: ability to access database, EFS, calibration data, etc.


def lambda_handler(event: dict, context):
    """Handler function"""
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    now = datetime.now()
    logger.info(f"Now time is: {now}")

    # Get the environment variables
    bucket = os.environ["S3_BUCKET"]
    prefix = os.environ["S3_KEY_PATH"]

    # Retrieves objects in the S3 bucket under the given prefix
    try:
        s3 = boto3.client("s3")
        object_list = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)["Contents"]
        logger.info(f"Object list: {object_list}")
    except KeyError:
        logger.warning("No files present.")

    # TODO: this state will change based on availability of data
    # TODO: we need to think about what needs to be passed into the container
    return {
        "STATE": "SUCCESS",
        "JOB_NAME": os.environ["PROCESSING_NAME"],
        "COMMAND": ["packet-ingest"],
    }
