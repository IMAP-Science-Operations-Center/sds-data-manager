"""Define lambda to support the download API."""

import json
import logging
import os
import re
from datetime import datetime

import boto3
import botocore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Entry point to the download API lambda.

    Check if this file exists or not. If file doesn't exist, it gives back an
    error. Otherwise, it returns pre-signed s3 url that user can use to download
    data from s3.

    To avoid any 307 redirects we use s3v4 signing method.
    This method includes the region in the URL, so when the user uploads a file,
    the URL will point directly to the correct regional S3 endpoint.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide information
        about the invocation, function, and runtime environment.

    Returns
    -------
    dict
        The response from the function which could either be a pre-signed
        S3 URL in case of successful operation or an error message with
        corresponding status code in case of failure.

    """
    query_params = event.get("queryStringParameters", {})
    print("queryStringParameters")
    print(query_params)
    start_str = query_params.get("start")
    end_str = query_params.get("end")

    try:
        start_time = datetime.strptime(start_str, "%Y%j%H%M%S")
        end_time = datetime.strptime(end_str, "%Y%j%H%M%S")
    except ValueError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Invalid date format. Expected format: YYYYDOYHHMMSS"}
            ),
        }

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    # TODO: may change this based on number of objects in directory.
    # Max 1000 objects can be listed in one call.
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix="logs/")
    print('response')
    print(response)
    matching_files = []

    for obj in response.get("Contents", []):
        # TODO: change filename to flight_iois_X.log.YYYY-DOYTHH:MM:SS.ssssss
        # Parse the timestamp from the filename (adjust this to your naming format)
        print(obj["Key"])
        match = re.search(r'(\d{4}-\d{3}T\d{2}-\d{2}-\d{2})_(\d{6})', obj["Key"])

        if not match:
            print(f"Skipping non-log key: {obj['Key']}")
            continue

        timestamp_str, microseconds = match.groups()
        file_date = datetime.strptime(timestamp_str, "%Y-%jT%H-%M-%S").replace(microsecond=int(microseconds))

        if start_time <= file_date <= end_time:
            matching_files.append(
                {
                    "Key": obj["Key"],
                    "LastModified": obj["LastModified"].isoformat(),
                    "Size": obj["Size"],
                }
            )

    response = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"files": matching_files}),
    }

    return response
