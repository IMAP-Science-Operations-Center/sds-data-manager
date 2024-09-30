"""IALiRT ingest lambda."""

import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

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
    # TODO: these steps will be put into different functions.
    logger.info("Received event: %s", json.dumps(event))

    ingest_table_name = os.environ.get("INGEST_TABLE")
    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    dynamodb = boto3.resource("dynamodb")
    ingest_table = dynamodb.Table(ingest_table_name)
    algorithm_table = dynamodb.Table(algorithm_table_name)

    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)

    # TODO: Each of these steps in temporary, but provides an idea
    #  of how the lambda will be used.
    # 1. Ingest Data to Ingest Table.
    item = {
        "apid": 478,
        "met": 123,
        "ingest_time": "2021-01-01T00:00:00Z",
        "packet_blob": b"binary_data_string",
    }

    ingest_table.put_item(Item=item)
    logger.info("Successfully wrote item to DynamoDB: %s", item)

    # 2. Query Ingest Table for previous times as required by instrument.
    response = ingest_table.query(KeyConditionExpression=Key("apid").eq(478))
    items = response["Items"]
    logger.info("Scan successful. Retrieved items: %s", items)

    # 3. After processing insert data into Algorithm Table.
    item = {
        "product_name": "hit_product_1",
        "met": 123,
        "insert_time": "2021-01-01T00:00:00Z",
        "data_product_1": str(1234.56),
    }
    algorithm_table.put_item(Item=item)
    logger.info("Successfully wrote item to DynamoDB: %s", item)
