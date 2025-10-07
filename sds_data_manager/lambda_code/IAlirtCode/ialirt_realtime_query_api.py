"""I-ALiRT Realtime Query API."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import botocore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Entry point to the query realtime objects.

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
    logger.info(f"Event: {event}")

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    now = datetime.now(timezone.utc)
    five_minutes_ago = now - timedelta(minutes=5)

    # Account for any cases in which data spans a threshold since
    # s3 only uses prefixes for queries.
    # Example:
    # now = 2026-01-01T00:02:00Z
    # five_minutes_ago = 2025-12-31T23:57:00Z
    first_prefix = five_minutes_ago.strftime("realtime/imap_ialirt_realtime_%Y-%jT%H")
    second_prefix = now.strftime("realtime/imap_ialirt_realtime_%Y-%jT%H")

    first_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=first_prefix)
    objects = first_response.get("Contents", [])

    if second_prefix != first_prefix:
        second_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=second_prefix)
        objects.extend(second_response.get("Contents", []))

    if not objects:
        logger.info("No realtime files found.")
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "No realtime files found in the last 5 minutes."}
            ),
        }

    # Pick the latest based on LastModified
    latest_obj = max(objects, key=lambda x: x["LastModified"])
    latest_key = latest_obj["Key"]

    logger.info(f"Latest file found: {latest_key}")

    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": latest_key},
        ExpiresIn=3600,
    )

    response = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "latest_file": os.path.basename(latest_key),
                "presigned_url": presigned_url,
            }
        ),
    }

    return response
