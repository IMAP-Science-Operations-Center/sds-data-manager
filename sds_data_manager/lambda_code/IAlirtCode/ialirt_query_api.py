"""Define lambda to support the download API."""

import json
import logging
import os
from datetime import datetime

import boto3
import botocore

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Entry point to the query API lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Notes
    -----
    Based on filename flight_iois_X.log.YYYY-DOYTHH:MM:SS.ssssss.txt.
    This is the log file produced by IOIS for each instance.

    Example
    -------
    Below is an event example:
    {
        "queryStringParameters": {
            "year": "2024",
            "doy": "141",
            "instance": "1"
        }
    }
    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    query_params = event["queryStringParameters"]
    year = query_params.get("year")
    doy = query_params.get("doy")
    instance = query_params.get("instance")

    try:
        day = datetime.strptime(f"{year}{doy}", "%Y%j")
    except ValueError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Invalid year or day format. Use YYYY and DOY."}
            ),
        }

    prefix = day.strftime(f"logs/flight_iois_{instance}.log.%Y-%j")

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    files = []

    for obj in response.get("Contents", []):
        filename = obj["Key"].split("/")[-1]
        files.append(filename)

    response = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"files": files}),
    }

    return response
