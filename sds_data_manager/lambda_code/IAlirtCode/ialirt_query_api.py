"""Define lambda to support the download API."""

import json
import logging
import os
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
    prefix = start_time.strftime("logs/%Y/%j/")

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    # TODO: may change this based on number of objects in directory.
    # Max 1000 objects can be listed in one call.
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)

    matching_files = []

    for obj in response.get("Contents", []):
        # Parse the timestamp from the filename (adjust this to your naming format)
        filename = obj["Key"].split("/")[-1]
        date_str = filename.split("_flight_")[1].split(".")[0]
        file_date = datetime.strptime(date_str, "%Y_%j_%H_%M_%S")

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
