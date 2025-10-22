"""IALiRT rsync failure checker lambda."""

import json
import logging
import os

import boto3
import botocore
from botocore.client import BaseClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def check_for_rsync_failure(s3_client: BaseClient, key: str, bucket: str) -> bool:
    """Scan recent log files in an S3 bucket for 'rsync' command failures.

    This function iterates through the specified log files stored in the given
    S3 bucket and checks for any occurrence of the string
    'command failed: rsync'. If found, it returns True immediately; otherwise,
    it returns False after checking all files.

    Parameters
    ----------
    s3_client : BaseClient
        A boto3 S3 client used to retrieve log objects from the bucket.
    key : str
        The S3 object key (filename) to scan for the failure message.
    bucket : str
        Name of the S3 bucket containing the log files.

    Returns
    -------
    bool
        True if any log file contains the string 'command failed: rsync';
        False otherwise.
    """
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    content = obj["Body"].read()
    if b"command failed: rsync" in content:
        logger.warning(f"Found rsync failure in {key}")
        return True
    return False


def notify_failure(topic_arn: str, key: str, bucket: str):
    """Publish SNS message directly if rsync failure detected.

    Parameters
    ----------
    topic_arn : str
        The ARN of the SNS topic to publish the alert to.
    key : str
        The filename to scan for the failure message.
    bucket : str
        Name of the S3 bucket containing the log files.
    """
    sns = boto3.client("sns")
    message = f"Rsync failure detected in log file: s3://{bucket}/{key}"
    sns.publish(
        TopicArn=topic_arn,
        Subject="I-ALiRT Rsync Failure Detected",
        Message=message,
    )
    logger.info(f"Published SNS alert to {topic_arn}")


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
    key = event["detail"]["object"]["key"]

    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    found = check_for_rsync_failure(s3_client, key, bucket)

    if found:
        notify_failure(os.environ["SNS_TOPIC_ARN"], key, bucket)

    return {"found_rsync_failure": found}
