"""IALiRT rsync failure checker lambda."""

import json
import logging
from datetime import datetime, timezone

import boto3
import botocore
from botocore.client import BaseClient

from .ialirt_realtime import query_filenames

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def check_for_rsync_failure(
    s3_client: BaseClient, filenames: list, bucket: str
) -> bool:
    """Scan recent log files in an S3 bucket for 'rsync' command failures.

    This function iterates through the specified log files stored in the given
    S3 bucket and checks for any occurrence of the string
    'command failed: rsync'. If found, it returns True immediately; otherwise,
    it returns False after checking all files.

    Parameters
    ----------
    s3_client : BaseClient
        A boto3 S3 client used to retrieve log objects from the bucket.
    filenames : list
        List of S3 object keys (filenames) to scan for the failure message.
    bucket : str
        Name of the S3 bucket containing the log files.

    Returns
    -------
    bool
        True if any log file contains the string 'command failed: rsync';
        False otherwise.
    """
    for key in filenames:
        obj = s3_client.get_object(Bucket=bucket, Key=f"logs/{key}")
        body = obj["Body"]
        for line in body.iter_lines():
            if b"command failed: rsync" in line:
                logger.warning(f"Found rsync failure in {key}")
                return True
    return False


def publish_failure_metric():
    """
    Publish a custom CloudWatch metric for rsync failure.

    This metric is used to trigger alarms when a failure is detected.
    """
    cloudwatch = boto3.client("cloudwatch")
    cloudwatch.put_metric_data(
        Namespace="IMAP/Ialirt",
        MetricData=[
            {
                "MetricName": "RsyncFailures",
                "Dimensions": [
                    {"Name": "Function", "Value": "ialirt-rsync-alarm"},
                ],
                "Unit": "Count",
                "Value": 1,
            },
        ],
    )
    logger.info("Published CloudWatch metric: IMAP/Ialirt::RsyncFailures = 1")


def lambda_handler(event, context):
    """Check for 'command failed: rsync' messages in recent logs.

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

    if "now" in event:
        now = datetime.fromisoformat(event["now"].replace("Z", "")).replace(
            tzinfo=timezone.utc
        )
    else:
        now = datetime.now(timezone.utc)

    filenames = query_filenames(s3_client, bucket, now)
    filenames = sorted(filenames)
    if not filenames:
        logger.info("No log files found in the last 48 hours.")
        return {"statusCode": 204, "body": ""}

    found = check_for_rsync_failure(s3_client, filenames, bucket)

    if found:
        publish_failure_metric()

    return {"found_rsync_failure": found}
