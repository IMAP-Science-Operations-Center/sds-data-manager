"""IALiRT ingest lambda."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Create metadata and add it to the database.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

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
    logger.info("Received event: %s", json.dumps(event))

    table_name = os.environ.get("TABLE_NAME")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)

    # TODO: item is temporary and will be replaced with actual packet data.
    item = {
        "met": 123,
        "ingest_time": "2021-01-01T00:00:00Z",
        "packet_blob": b"binary_data_string",
    }

    table.put_item(Item=item)
    logger.info("Successfully wrote item to DynamoDB: %s", item)
