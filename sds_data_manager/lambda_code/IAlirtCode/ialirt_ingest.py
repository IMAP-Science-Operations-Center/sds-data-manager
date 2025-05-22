"""IALiRT ingest lambda."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import botocore
import xarray as xr
from boto3.dynamodb.conditions import Key
from imap_processing import imap_module_directory
from imap_processing.ialirt.l0.process_hit import process_hit
from imap_processing.ialirt.l0.process_swe import process_swe
from imap_processing.utils import packet_file_to_datasets

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


def query_filenames(bucket: str, region: str, now: datetime):
    """Query the packets in the s3 bucket.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.
    now : datetime
        The current time in UTC.

    Returns
    -------
    filenames : list
        List of file paths.
    """
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    five_minutes_ago = now - timedelta(minutes=5)

    # Account for any cases in which data spans a threshold since
    # s3 only uses prefixes for queries.
    # Example:
    # now = 2026-01-01T00:02:00Z
    # five_minutes_ago = 2025-12-31T23:57:00Z
    first_prefix = five_minutes_ago.strftime("packets/iois_1_packets_%Y_%j_%H_")
    second_prefix = now.strftime("packets/iois_1_packets_%Y_%j_%H_")

    first_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=first_prefix)
    objects = first_response.get("Contents", [])

    if second_prefix != first_prefix:
        second_response = s3_client.list_objects_v2(Bucket=bucket, Prefix=second_prefix)
        objects.extend(second_response.get("Contents", []))

    filenames = []
    for obj in objects:
        key = obj["Key"]
        timestamp_str = key.split("iois_1_packets_")[1]
        timestamp = datetime.strptime(timestamp_str, "%Y_%j_%H_%M_%S")
        timestamp = timestamp.replace(tzinfo=timezone.utc)

        if five_minutes_ago <= timestamp <= now:
            filenames.append(key)

    return filenames


def parse_packets(filenames):
    """Get packets into datasets and combine.

    This function is an event handler for s3 ingest bucket.
    It is also used to ingest data to the DynamoDB table.

    Parameters
    ----------
    filenames : list
        List of file paths.

    Returns
    -------
    combined : xr.Dataset
        Combined dataset.
    """
    xtce_ialirt_path = (
        imap_module_directory / "ialirt" / "packet_definitions" / "ialirt.xml"
    )
    apid = 478
    datasets = []

    for packet_path in filenames:
        xarray_data = packet_file_to_datasets(
            packet_path, xtce_ialirt_path, use_derived_value=False
        )[apid]
        datasets.append(xarray_data)

    combined = xr.concat(datasets, dim="epoch")

    return combined


def process_algorithms(combined, algorithm_table):
    processors = [
        ("hit", process_hit),
        ("swe", process_swe),
    ]

    for prefix, process_func in processors:
        insert_data(process_func(combined), algorithm_table, prefix)


def insert_data(data: list[dict], algorithm_table, product_prefix: str):
    apid = data[0]["apid"]
    mets = [item["met"] for item in data]
    min_met = min(mets)
    max_met = max(mets)

    # Query existing items.
    response = algorithm_table.query(
        KeyConditionExpression=Key("apid").eq(apid)
        & Key("met").between(min_met, max_met)
    )

    existing_items = {item["met"]: item for item in response.get("Items", [])}

    # Insert or update as needed
    for raw in data:
        met = raw["met"]
        key = {"apid": apid, "met": met}
        existing = existing_items.get(met)

        if existing:
            if any(k.startswith(product_prefix) for k in existing.keys()):
                logger.info(
                    f"{product_prefix.upper()} data already exists for met={met}. Skipping."
                )
                continue

            update_expr = "SET " + ", ".join(
                f"{k} = :{k}" for k in raw if k not in {"apid", "met"}
            )
            expr_vals = {f":{k}": v for k, v in raw.items() if k not in {"apid", "met"}}

            algorithm_table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_vals,
            )
            logger.info(f"Updated met={met} with {product_prefix.upper()} data.")
        else:
            algorithm_table.put_item(Item=raw)
            logger.info(f"Inserted new {product_prefix.upper()} item for met={met}.")


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
    logger.info("Received event: %s", json.dumps(event))

    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    dynamodb = boto3.resource("dynamodb")
    algorithm_table = dynamodb.Table(algorithm_table_name)

    bucket = event["detail"]["bucket"]["name"]
    region = event["region"]

    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)
    logger.info("Retrieved filename: %s", filename)

    # Query s3 for packet filenames from past 5 minutes.
    now = datetime.now(timezone.utc)
    filenames = query_filenames(bucket, region, now)

    # Get packets into datasets and combine.
    combined = parse_packets(filenames)
    # Process algorithms and insert new data.
    process_algorithms(combined, algorithm_table)

    logger.info("Successfully wrote all new items to DynamoDB")
