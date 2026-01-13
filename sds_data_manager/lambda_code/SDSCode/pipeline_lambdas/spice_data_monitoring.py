"""Lambda function to monitor SPICE data freshness.

This Lambda checks for missing SPICE data by monitoring specific S3
prefixes and publishing CloudWatch metrics when data is stale.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

# AWS Clients
S3_CLIENT = boto3.client("s3")
CLOUDWATCH_CLIENT = boto3.client("cloudwatch")

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration from environment variables
BUCKET_NAME = os.environ.get("S3_BUCKET")
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "IMAP/SpiceDataFreshness")

# Monitored prefixes configuration
# Format: {prefix: {name: display_name, threshold_days: N}}
MONITORED_PREFIXES = {
    "imap/spice/ck/": {
        "name": "CK_Kernels",
        "threshold_days": int(os.environ.get("CK_THRESHOLD_DAYS", "7")),
        "description": "Attitude history and pointing attitude kernels",
    },
    "imap/spice/spin/": {
        "name": "Spin_Files",
        "threshold_days": int(os.environ.get("SPIN_THRESHOLD_DAYS", "7")),
        "description": "Spacecraft spin files",
    },
    "imap/spice/sclk/": {
        "name": "SCLK_Kernels",
        "threshold_days": int(os.environ.get("SCLK_THRESHOLD_DAYS", "7")),
        "description": "Spacecraft clock kernels",
    },
}


def get_most_recent_file_age(bucket: str, prefix: str) -> int | None:
    """Get the age in days of the most recent file in an S3 prefix.

    Parameters
    ----------
    bucket : str
        S3 bucket name
    prefix : str
        S3 prefix to check

    Returns
    -------
    int | None
        Number of days since the most recent file was modified,
        or None if no files exist
    """
    try:
        # List objects in the prefix, sorted by LastModified descending
        response = S3_CLIENT.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=1000,  # Get enough files to find the most recent
        )

        if "Contents" not in response or not response["Contents"]:
            logger.warning(f"No files found in prefix: {prefix}")
            return None

        # Find the most recent file
        most_recent = max(response["Contents"], key=lambda x: x["LastModified"])
        last_modified = most_recent["LastModified"]

        # Calculate age in days
        age = (datetime.now(timezone.utc) - last_modified).days
        logger.info(
            f"Prefix {prefix}: Most recent file is {age} days old "
            f"(modified: {last_modified})"
        )

        return age

    except Exception as e:
        logger.error(f"Error checking prefix {prefix}: {e!s}")
        return None


def publish_metric(prefix_name: str, days_old: int):
    """Publish a CloudWatch metric for SPICE data freshness.

    Parameters
    ----------
    prefix_name : str
        Name of the prefix being monitored
    days_old : int
        Number of days since the most recent file
    """
    try:
        CLOUDWATCH_CLIENT.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "DaysSinceLastSPICEFile",
                    "Value": days_old,
                    "Unit": "None",
                    "Dimensions": [{"Name": "Prefix", "Value": prefix_name}],
                    "Timestamp": datetime.now(timezone.utc),
                }
            ],
        )
        logger.info(f"Published metric for {prefix_name}: {days_old} days old")
    except Exception as e:
        logger.error(f"Error publishing metric for {prefix_name}: {e!s}")


def lambda_handler(event, context):
    """Lambda handler to check SPICE data freshness.

    This function runs on a schedule (daily) and checks each monitored
    SPICE prefix for the most recent file. It publishes CloudWatch
    metrics that can be used to trigger alarms.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process. Source is EventBridge scheduled rule.
    context : obj
        The context object for the lambda function

    Returns
    -------
    dict
        Response with status code and summary of results
    """
    logger.info(f"Received event: {event}")
    logger.info(f"Checking SPICE data freshness in bucket: {BUCKET_NAME}")

    results = {}

    for prefix, config in MONITORED_PREFIXES.items():
        prefix_name = config["name"]
        threshold = config["threshold_days"]
        description = config["description"]

        logger.info(
            f"Checking {prefix_name} ({description}) with threshold {threshold} days"
        )

        days_old = get_most_recent_file_age(BUCKET_NAME, prefix)

        if days_old is None:
            # No files found - use a sentinel value
            days_old = 999
            logger.warning(f"{prefix_name}: No files found in prefix {prefix}")

        # Publish the metric regardless of threshold
        publish_metric(prefix_name, days_old)

        # Store result
        results[prefix_name] = {
            "days_old": days_old,
            "threshold": threshold,
            "stale": days_old > threshold,
        }

    # Log summary
    stale_prefixes = [name for name, result in results.items() if result["stale"]]
    if stale_prefixes:
        logger.warning(f"Stale data detected in: {', '.join(stale_prefixes)}")
    else:
        logger.info("All monitored prefixes are up to date")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"message": "SPICE data check complete", "results": results}
        ),
    }
