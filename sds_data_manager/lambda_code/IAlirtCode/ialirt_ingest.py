"""IALiRT ingest lambda."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import botocore
from imap_processing import imap_module_directory
from imap_processing.utils import packet_file_to_datasets

# TODO: from imap_processing.ialirt.l0.generate_binary import generate_binary

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def ingest_binary(ingest_table):
    # Example format (TODO: comment out):
    items = [
        {
            "apid": 478,
            "met": 123,
            "ingest_time": "2021-01-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        }
    ]
    packet_definition = imap_module_directory / "ialirt/packet_definitions/ialirt.xml"
    # items = generate_binary(s3_filepath, packet_definition)

    with ingest_table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)

    logger.info("Successfully wrote item to DynamoDB: %s", item)


def parse_packet(filename: str, bucket: str, key: str, download_dir: Path):
    """Parse the packet.

    Parameters
    ----------
    filename : str
        The name of the file to be downloaded from S3.
    bucket : str
        The name of the S3 bucket.
    key : str
        The key of the file in the S3 bucket.
    download_dir : Path
        The directory where the file will be downloaded.

    Returns
    -------
    datasets_by_apid : xr.Dataset
        Parsed dataset.
    """
    local_path = os.path.join(download_dir, filename)

    s3 = boto3.client("s3")
    s3.download_file(bucket, key, local_path)
    logger.info("Downloaded file to %s", local_path)

    xtce = os.path.join(imap_module_directory, "ialirt.xml")

    datasets_by_apid = packet_file_to_datasets(local_path, xtce)

    return datasets_by_apid


def query_filenames(bucket: str, region: str):
    """Entry point to the query API lambda."""
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
    first_prefix = five_minutes_ago.strftime("packets/iois_1_packets_%Y_%j_%H_")
    second_prefix = now.strftime("packets/iois_1_packets_%Y_%j_%H_")

    first_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=first_prefix)
    second_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=second_prefix)

    objects = []
    objects.extend(first_response.get("Contents", []))
    objects.extend(second_response.get("Contents", []))

    filenames = []
    for obj in objects:
        key = obj["Key"]
        timestamp_str = key.split("iois_1_packets_")[1]
        timestamp = datetime.strptime(timestamp_str, "%Y_%j_%H_%M_%S")

        if five_minutes_ago <= timestamp <= now:
            filenames.append(key)

    return filenames


def lambda_handler(event, context):
    """Create metadata and add it to the database.

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
    # TODO: these steps will be put into different functions.
    logger.info("Received event: %s", json.dumps(event))

    ingest_table_name = os.environ.get("INGEST_TABLE")
    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    dynamodb = boto3.resource("dynamodb")
    ingest_table = dynamodb.Table(ingest_table_name)
    algorithm_table = dynamodb.Table(algorithm_table_name)
    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("REGION")

    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)

    # 1. Ingest Data to Ingest Table.
    # TODO: this might be better as a class.
    ingest_binary(ingest_table)

    # 2. Query s3 for packet filenames from past 5 minutes.
    query_filenames(bucket, region)
    # TODO: will use filenames here.

    # TODO: stopped here.
    # 3. After processing insert data into Algorithm Table.
    item = {
        "apid": 478,
        "met": 123,
        "insert_time": "2021-01-01T00:00:00Z",
        "product_name": "hit_product_1",
        "data_product_1": str(1234.56),
    }
    algorithm_table.put_item(Item=item)
    logger.info("Successfully wrote item to DynamoDB: %s", item)
