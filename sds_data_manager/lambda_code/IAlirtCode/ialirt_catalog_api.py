"""Define the lambda function that supports the catalog API."""

import json
import logging
import os
from datetime import datetime, timedelta

import boto3
import botocore

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context) -> dict:
    """Entry point to the catalog API lambda.

    Takes in a date range and ground station, and queries the s3 bucket that holds
    the pointing schedules for ground stations part of the I-ALiRT network.

    If files exist for the requested date range, the response body returns all file
    versions for every date, including the file name and its last date modified.

    An example of a query to this endpoint as a URL is:
    "https://ialirt.dev.imap-mission.com/ialirt-catalog?start_date=2025-02-06&end_date=2025-02-07&station=test_station"

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process, i.e., date range and ground station
        Ex:
            event = {
                "queryStringParameters": {
                    "start_date": "2025-02-06",
                    "end_date": "2025-02-07",
                    "station": "station1",
                }
            }
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Returns
    -------
    dict
        A dictionary containing headers, status code, and body, designed to be returned
        as an API response.
        Ex:
        {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': '{
                "2025-02-06":
                    {"file_names":
                        ["20250206_station1_01.txt", "20250206_station1_02.txt"],
                     "dates_modified":
                        ["2025-02-12 18:37", "2025-02-12 18:37"]}
            }'
        }
    """
    logger.info("Received event: " + json.dumps(event, indent=2))

    query_params = event["queryStringParameters"]
    day = datetime.strptime(query_params.get("start_date"), "%Y-%m-%d")
    end_date = datetime.strptime(query_params.get("end_date"), "%Y-%m-%d")
    station = query_params.get("station")

    if not day < end_date:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": "The start date of the requested time frame is not before the "
            "end date. Please supply a valid time range.",
        }

    if end_date - day > timedelta(days=30):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": "The end date of the requested time frame must not be more "
            "than 30 days after the start date. Please supply a valid time"
            " range.",
        }

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    response_body = {}
    while day < end_date:
        day_path = f"pointing_schedules/{station}/{day.strftime('%Y%m%d')}/"
        files = s3_client.list_objects_v2(Bucket=bucket, Prefix=day_path)
        if "Contents" not in files.keys():
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": "There are not files associated with the provided date {day}. "
                "Please supply a valid time range that is covered by existing "
                "files.",
            }
        file_names = []
        dates_modified = []
        for file in files["Contents"]:
            file_names.append(file["Key"].rsplit("/", 1)[1])
            dates_modified.append(file["LastModified"].strftime("%Y-%m-%d %H:%M"))
        response_body[f"{day}"] = {
            "file_names": file_names,
            "dates_modified": dates_modified,
        }
        day += timedelta(days=1)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }
