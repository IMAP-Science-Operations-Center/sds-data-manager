"""IALiRT algorithm lambda."""

import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")


def lambda_handler(event, context):
    """Query/ingest data to the DynamoDB tables.

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
    ingest_table_name = os.environ.get("INGEST_TABLE")
    ingest_table = dynamodb.Table(ingest_table_name)
    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    algorithm_table = dynamodb.Table(algorithm_table_name)
    ingest_table.put_item(
        Item={
            "apid": 478,
            "met": 123,
            "ingest_time": "2021-01-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        }
    )

    response = ingest_table.query(KeyConditionExpression=Key("instrument").eq("hit"))

    items = response["Items"]
    logger.info("Scan successful. Retrieved items: %s", items)

    key = event["detail"]["object"]["key"]
    print(key)

    # TODO: item is temporary and will be replaced with actual data products.
    item = {
        "instrument": "hit",
        "met": 123,
        "insert_time": "2021-01-01T00:00:00Z",
        "data_product_1": str(1234.56),
    }

    algorithm_table.put_item(Item=item)
    logger.info("Successfully wrote item to DynamoDB: %s", item)
