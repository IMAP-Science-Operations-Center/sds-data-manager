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


def lambda_handler(event, context):  # noqa: PLR0912
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
    Below is an event example using year/month/day:
    {
        "queryStringParameters": {
            "year": "2024",
            "month": "05",
            "day": "21",
            "version": "1"
        }
    }

    Or using since to get all files on or after a date:
    {
        "queryStringParameters": {
            "since": "20240521",
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
    since = query_params.get("since")

    if since and (year or month or day):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "since cannot be combined with year, month, or day."}
            ),
        }

    if not since and ((day and not month) or (month and not year)):
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

    since_date = None
    if since:
        try:
            since_date = datetime.strptime(since, "%Y%m%d").date()
        except ValueError:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "error": (
                            "Invalid since format. Must be YYYYMMDD (e.g. 20240521)."
                        )
                    }
                ),
            }

    date_prefix = ""
    if not since:
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

    paginator = s3_client.get_paginator("list_objects_v2")
    files = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            filename = obj["Key"].split("/")[-1]
            if version_str not in filename:
                continue
            if since_date is not None:
                file_date_str = filename.split("_realtime_")[1][:8]
                file_date = datetime.strptime(file_date_str, "%Y%m%d").date()
                if file_date < since_date:
                    continue
            files.append(filename)

    files.sort()

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"files": files}),
    }
