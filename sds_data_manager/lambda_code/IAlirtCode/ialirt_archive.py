"""IALiRT archive lambda."""

import json
import logging
import os
from datetime import datetime, timedelta

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Query database and generate cdf.

    This function is an event handler for a cron job.
    It is used to query the DynamoDB table, generate a cdf,
    and put it in s3.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Returns
    -------
    response : dict
        The response from the DynamoDB query.
    """
    logger.info("Received event: %s", json.dumps(event))

    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    dynamodb = boto3.resource("dynamodb")
    algorithm_table = dynamodb.Table(algorithm_table_name)

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    start_iso = yesterday.isoformat()
    end_iso = now.isoformat()

    # Query using insert_time GSI
    response = algorithm_table.query(
        IndexName="insert_time",
        KeyConditionExpression="apid = :apid_val AND "
        "insert_time BETWEEN :start AND :end",
        ExpressionAttributeValues={
            ":apid_val": 478,
            ":start": start_iso,
            ":end": end_iso,
        },
    )

    # TODO: create a cdf and put in S3

    return response
