"""IALiRT archive lambda."""

import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import boto3
import imap_data_access
from boto3.dynamodb.conditions import Key
from imap_processing.cdf.utils import write_cdf
from imap_processing.ialirt.utils.create_xarray import create_xarray_from_records

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


INSTRUMENTS = ["mag", "codice_lo", "codice_hi", "hit", "swe", "swapi", "spacecraft"]


def query_instrument(
    data_table, instrument: str, start_iso: str, end_iso: str
) -> list[dict]:
    """Query database and handles pagination.

    Parameters
    ----------
    data_table : ddb.Table
        Algorithm database table.
    instrument : str
        Instrument name.
    start_iso : str
        Start date of query.
    end_iso : str
        End date of query.

    Returns
    -------
    items : list
        Items queried for the instrument.
    """
    items: list[dict] = []
    last_key = None

    while True:
        kwargs = dict(
            KeyConditionExpression=(
                Key("instrument").eq(instrument)
                & Key("time_utc").between(start_iso, end_iso)
            ),
        )
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = data_table.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return items


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
    """
    logger.info("Received event: %s", json.dumps(event))

    imap_data_access.config["DATA_DIR"] = Path("/tmp")  # noqa: S108

    data_table_name = os.environ.get("DATA_TABLE")
    dynamodb = boto3.resource("dynamodb")
    data_table = dynamodb.Table(data_table_name)
    bucket = os.environ.get("S3_BUCKET")
    region = os.environ.get("AWS_REGION")

    # Query 1 day's worth of data a week ago.
    now_override = event.get("now_utc")
    if now_override:
        now = datetime.fromisoformat(now_override).astimezone(timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    target_date = (now - timedelta(days=7)).date()

    # This is in case the solid state recorder is setup to save
    # I-ALiRT data onboard in which case DSN will deliver the data in batches
    # approximately 3 times per week (instead of having all data be
    # in near-realtime).
    seven_days_ago = datetime.combine(
        target_date, time.min, tzinfo=timezone.utc
    )  # 00:00 UTC
    one_week = seven_days_ago + timedelta(days=1)  # next midnight

    buffer = timedelta(minutes=5)

    start_iso = (seven_days_ago - buffer).isoformat()
    end_iso = (one_week + buffer).isoformat()

    all_items = []
    for inst in INSTRUMENTS:
        inst_items = query_instrument(data_table, inst, start_iso, end_iso)
        logger.info("%s: %d items", inst, len(inst_items))
        all_items.extend(inst_items)

    if not all_items:
        logger.info(
            "No I-ALiRT items found between %s and %s; skipping CDF write.",
            start_iso,
            end_iso,
        )
        return
    dataset = create_xarray_from_records(all_items)
    dataset.attrs["Data_version"] = "001"
    dataset.attrs["Start_date"] = seven_days_ago.strftime("%Y%m%d")
    test_data_path = write_cdf(
        dataset, istp=True, compression=None, auto_fix_depends=False
    )

    output_key = f"archive/{test_data_path.name}"

    s3_client = boto3.client("s3", region_name=region)
    s3_client.upload_file(
        Filename=str(test_data_path),
        Bucket=bucket,
        Key=output_key,
        ExtraArgs={"ContentType": "application/x-cdf"},
    )
    logger.info(f"Uploaded archive file to s3://{bucket}/{output_key}")
