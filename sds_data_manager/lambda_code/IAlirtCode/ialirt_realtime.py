"""IALiRT real-time ingest plots lambda."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import botocore
from botocore.client import BaseClient
from imap_processing.ialirt.calculate_ingest import format_ingest_data

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# I-ALiRT packets are on VCID 5. Other VCIDs are frames with corrupted headers.
# Example:
# 2026/276-18:14:02.117 Error: dropout in VCDU counter for VCID=5!
# previous: 148, current: 150
DROPOUT_PATTERN = re.compile(
    r"^(?P<time>\S+) Error: dropout in VCDU counter for VCID=(?P<vcid>\d+)!"
    r"\s+previous: (?P<previous>\d+), current: (?P<current>\d+)"
)


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
        prefix = current.strftime("logs/flight_iois_1.log.%Y-%jT%H")
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
            decoded = line.decode("utf-8")
            if decoded:
                all_lines.append(decoded)

    return all_lines


def find_dropouts(lines: list) -> list:
    """Find I-ALiRT frame dropouts reported in the IOIS logs.

    Parameters
    ----------
    lines : list
        All lines of the log files.

    Returns
    -------
    dropouts : list
        Ground time of each dropout and the number of missing frames.
    """
    sending = False
    dropouts = []

    for line in lines:
        # Station rows of the periodic status report, e.g.
        # 17  Censipam      278-06:57:27  277-12:58:57    0.0
        parts = line.split()
        if "Periodic status report" in line:
            sending = False
        elif parts and parts[0].isdigit():
            sending = sending or float(parts[-1]) > 0

        # Only count dropouts while a station is sending data.
        match = DROPOUT_PATTERN.match(line)
        if not match or match["vcid"] != "5" or not sending:
            continue

        # The frame counter is 8 bits.
        missing = (int(match["current"]) - int(match["previous"]) - 1) % 256
        if missing:
            time = datetime.strptime(match["time"], "%Y/%j-%H:%M:%S.%f")
            dropouts.append(
                {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "missing_frames": missing,
                }
            )

    return dropouts


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
    all_lines = read_ingest_logs(s3_client, filenames, bucket)

    formatted = format_ingest_data(filenames[-1], all_lines)
    formatted["dropouts"] = find_dropouts(all_lines)
    name = Path(filenames[-1]).name
    timestamp = name.split(".", 2)[-1]
    output_key = f"realtime/imap_ialirt_realtime_{timestamp}.json"

    s3_client.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=json.dumps(formatted, indent=2)
        .replace("-Infinity", "null")
        .replace("Infinity", "null")
        .replace("NaN", "null")
        .encode("utf-8"),
        ContentType="application/json",
    )

    logger.info("Generated file %s.", output_key)
