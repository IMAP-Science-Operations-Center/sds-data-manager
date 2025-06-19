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
    Based on filename iois_1_packets_YYYY_DOY_HH_MM_SS.bin.
    Allows partial time specification (YYYY, DOY[, HH[, MM[, SS]]]).
    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    query_params = event["queryStringParameters"]
    year = query_params.get("year")
    doy = query_params.get("doy")
    hh = query_params.get("hh")
    mm = query_params.get("mm")
    ss = query_params.get("ss")

    if not year or not doy:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "At minimum, 'year' and 'doy' must be provided."}
            ),
        }

    try:
        datetime.strptime(f"{year}{doy}", "%Y%j")
    except ValueError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Invalid year or day format. Use YYYY and DOY."}
            ),
        }

    parts = [year, doy]
    if hh:
        parts.append(hh.zfill(2))
    if mm:
        parts.append(mm.zfill(2))
    if ss:
        parts.append(ss.zfill(2))

    # New prefix format based on filename convention
    prefix = "packets/iois_1_packets_" + "_".join(parts)

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
