"""IALiRT archive lambda."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import imap_data_access
from boto3.dynamodb.conditions import Key
from imap_processing.cdf.utils import write_cdf
from imap_processing.ialirt.utils.create_xarray import create_xarray_from_records

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Query database and generate cdf.

    This function is an event handler for a cron job.
    It is used to query the DynamoDB table, generate a cdf,
    and put it in s3.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Returns
    -------
    response : dict
        The response from the DynamoDB query.
    """
    logger.info("Received event: %s", json.dumps(event))

    imap_data_access.config["DATA_DIR"] = Path("/tmp")  # noqa: S108

    algorithm_table_name = os.environ.get("ALGORITHM_TABLE")
    dynamodb = boto3.resource("dynamodb")
    algorithm_table = dynamodb.Table(algorithm_table_name)
    bucket = os.environ.get("S3_BUCKET")
    region = os.environ.get("AWS_REGION")

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    start_iso = yesterday.isoformat()
    end_iso = now.isoformat()

    # Query using utc GSI
    response = algorithm_table.query(
        IndexName="last_modified",
        KeyConditionExpression=(
            Key("apid").eq(478) & Key("last_modified").between(start_iso, end_iso)
        ),
    )

    if not response["Items"]:
        logger.info("No new data to process.")
        return response

    dataset = create_xarray_from_records(response["Items"])
    dataset.attrs["Data_version"] = "000"
    dataset.attrs["Start_date"] = yesterday.strftime("%Y%m%d")
    test_data_path = write_cdf(dataset, istp=True)

    output_key = f"archive/{test_data_path.name}"

    s3_client = boto3.client("s3", region_name=region)
    s3_client.upload_file(
        Filename=str(test_data_path),
        Bucket=bucket,
        Key=output_key,
        ExtraArgs={"ContentType": "application/x-cdf"},
    )
    logger.info(f"Uploaded coverage table to s3://{bucket}/{output_key}")

    return response
