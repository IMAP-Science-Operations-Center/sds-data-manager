"""Define lambda to support the download API."""

import json
import logging
import os

import boto3
import botocore

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Entry point to the archive query API lambda.

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
    Based on filename imap_ialirt_l1_realtime_<yyyymmdd>_v001.cdf.
    All parameters are optional. Defaults to listing all version 1 files.

    Example
    -------
    Below is an event example:
    {
        "queryStringParameters": {
            "year": "2024",
            "month": "05",
            "day": "21",
            "version": "1"
        }
    }
    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    query_params = event.get("queryStringParameters") or {}
    year = query_params.get("year")
    month = query_params.get("month")
    day = query_params.get("day")
    version = query_params.get("version", "1")

    if (day and not month) or (month and not year):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Date parts must be specified in order: year, month, day."}
            ),
        }

    try:
        version_str = f"v{int(version):03d}"
    except ValueError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Invalid version format. Must be an integer."}
            ),
        }

    date_prefix = ""
    if year:
        date_prefix += year.zfill(4)
    if month:
        date_prefix += month.zfill(2)
    if day:
        date_prefix += day.zfill(2)

    prefix = f"archive/imap_ialirt_l1_realtime_{date_prefix}"

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
        if version_str in filename:
            files.append(filename)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"files": files}),
    }
