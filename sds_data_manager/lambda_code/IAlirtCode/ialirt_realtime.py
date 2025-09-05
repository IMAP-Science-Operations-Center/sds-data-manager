"""IALiRT real-time ingest plots lambda."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import botocore
from botocore.client import BaseClient
from imap_processing.ialirt.calculate_ingest import format_ingest_data

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def query_filenames(s3_client: BaseClient, bucket: str, now: datetime):
    """Query the packets in the s3 bucket.

    Parameters
    ----------
    s3_client : BaseClient
        The S3 client to interact with the S3 service.
    bucket : str
        The name of the S3 bucket.
    now : datetime
        The current time in UTC.

    Returns
    -------
    filenames : list
        List of file paths.
    """
    past_time = now - timedelta(hours=48)

    filenames = []

    for hour_offset in range(48 + 1):  # +1 to include the current hour
        current = past_time + timedelta(hours=hour_offset)
        prefix = current.strftime("logs/flight_iois_1.log.%Y-%jT%H_")
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)

        for obj in response.get("Contents", []):
            key = obj["Key"].replace("logs/", "", 1)
            filenames.append(key)

    return filenames


def read_ingest_logs(s3_client: BaseClient, filenames: list, bucket: str):
    """Read the logs in s3 bucket.

    Parameters
    ----------
    s3_client : BaseClient
        The S3 client to interact with the S3 service.
    filenames : list
        List of file paths.
    bucket : str
        The name of the S3 bucket.

    Returns
    -------
    all_lines : list
        List of file contents.
    """
    all_lines = []

    for key in filenames:
        obj = s3_client.get_object(Bucket=bucket, Key=f"logs/{key}")
        body = obj["Body"]
        for line in body.iter_lines():
            all_lines.append(line.decode("utf-8"))

    return all_lines


def lambda_handler(event, context):
    """Create near real-time ingest json files.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

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
    logger.info("Received event: %s", json.dumps(event))

    bucket = event["detail"]["bucket"]["name"]
    region = event["region"]

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    filenames = query_filenames(s3_client, bucket, datetime.now(timezone.utc))
    filenames = sorted(filenames)
    all_lines = read_ingest_logs(s3_client, filenames, bucket)

    formatted = format_ingest_data(filenames[-1], all_lines)
    name = Path(filenames[-1]).name
    timestamp = name.split(".", 2)[-1]
    output_key = f"realtime/imap_ialirt_realtime_{timestamp}.json"

    s3_client.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=json.dumps(formatted, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info("Generated file %s.", output_key)
