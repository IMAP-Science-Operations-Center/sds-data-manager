"""Define lambda to support the download API."""

import json
import logging
import os
import re
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
    Based on filename flight_iois_1.log.YYYY-DOYTHH-MM-SS_ssssss.txt
    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    query_params = event["queryStringParameters"]
    year = query_params.get("year")
    doy = query_params.get("doy")

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
    prefix = day.strftime("logs/flight_iois_1.log.%Y%j")

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    print('response')
    print(response)
    matching_files = []

    for obj in response.get("Contents", []):

        filename = obj["Key"].split("/")[-1]
        match = re.search(r'(\d{4}-\d{3}T\d{2}-\d{2}-\d{2})_(\d{6})', obj["Key"])

        if not match:
            logger.info(f"Skipping non-log key: {obj['Key']}")
            continue

        timestamp_str, microseconds = match.groups()
        file_date = datetime.strptime(timestamp_str, "%Y-%jT%H-%M-%S").replace(microsecond=int(microseconds))

        if start_time <= file_date <= end_time:
            matching_files.append(filename)

    response = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"files": matching_files}),
    }

    return response
