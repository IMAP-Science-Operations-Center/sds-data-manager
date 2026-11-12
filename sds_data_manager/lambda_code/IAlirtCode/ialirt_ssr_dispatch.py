"""Dispatch I-ALiRT SSR processing to the instrument Lambdas."""

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

import boto3
import imap_data_access
import spiceypy
from boto3.dynamodb.conditions import Key
from ialirt_ssr_processing import (
    DOWNLOAD_DIR,
    TIME_KERNELS,
    IalirtDataGapError,
    check_day_coverage,
)
from ialirt_ssr_utils import (
    download_spice_files,
    get_latest_spice_kernels,
    parse_packets,
    query_filenames,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# One Lambda per instrument, plus spacecraft status and position/velocity.
SSR_INSTRUMENTS = [
    "mag",
    "hit",
    "swe",
    "codice_lo",
    "codice_hi",
    "swapi",
    "spacecraft_status",
    "spacecraft",
]
DEFAULT_VERSION = "v001"


def function_name(instrument: str) -> str:
    """Return the Lambda function name for an instrument."""
    return f"ialirt-ssr-{instrument.replace('_', '-')}"


def get_days(detail: dict, instruments: list[str]) -> dict[str, list[str]]:
    """Return the days to process and the instruments to invoke for each.

    Parameters
    ----------
    detail : dict
        The EventBridge event detail. Empty for the daily trigger, or
        {"start_date": "YYYYMMDD", "end_date": "YYYYMMDD"} (both inclusive)
        for the manual trigger.
    instruments : list[str]
        The instruments to invoke for every day of a manual request.

    Returns
    -------
    days : dict[str, list[str]]
        Instruments to invoke, keyed by day in YYYYMMDD format.
    """
    if "start_date" in detail:
        start = datetime.strptime(detail["start_date"], "%Y%m%d")
        end = datetime.strptime(detail["end_date"], "%Y%m%d")
        if end < start:
            raise ValueError(f"end_date {end} is before start_date {start}")
    else:
        return get_daily_days()

    n_days = (end - start).days + 1
    return {
        (start + timedelta(days=i)).strftime("%Y%m%d"): instruments
        for i in range(n_days)
    }


def get_daily_days() -> dict[str, list[str]]:
    """Return the days and instruments for the daily trigger.

    Returns
    -------
    days : dict[str, list[str]]
        Instruments to invoke, keyed by day in YYYYMMDD format.
    """
    # Unreleased L0 files are only returned to queries with an API key.
    ssm_response = boto3.client("ssm").get_parameter(
        Name=os.environ["SSM_API_KEY_PARAMETER"], WithDecryption=True
    )
    imap_data_access.config["API_KEY"] = ssm_response["Parameter"]["Value"]
    table = boto3.resource("dynamodb").Table(os.environ["SSR_TABLE"])
    # /tmp persists between warm invocations.
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    DOWNLOAD_DIR.mkdir(parents=True)

    now = datetime.now(timezone.utc)
    # Query to see if any new packets arrived in the past 7 days.
    results = imap_data_access.query(
        instrument="ialirt",
        data_level="l0",
        descriptor="raw",
        ingestion_start_date=(now - timedelta(days=7)).strftime("%Y%m%d"),
    )
    # Get current day, past day, and future day
    days = {
        datetime.strptime(result["start_date"], "%Y%m%d") + timedelta(days=i)
        for result in results
        for i in (-1, 0, 1)
    }
    daily_days = {}

    for day in sorted(day.strftime("%Y%m%d") for day in days):
        # Check if any instrument in the database has data for this day.
        missing = [
            instrument
            for instrument in SSR_INSTRUMENTS
            if not is_processed(table, instrument, day, DEFAULT_VERSION)
        ]
        if not missing:
            logger.info("%s already processed for all instruments, skipping", day)
            continue
        # Check that all packets required to process are complete
        if is_complete(day):
            logger.info("%s complete, dispatching %s", day, missing)
            daily_days[day] = missing
    return daily_days


def is_processed(table, instrument: str, day: str, version: str) -> bool:
    """Check if the SSR table has any items for the instrument and day.

    Parameters
    ----------
    table : boto3 DynamoDB Table
        The SSR table.
    instrument : str
        The instrument name, e.g. "mag".
    day : str
        The day in YYYYMMDD format.
    version : str
        The data version, e.g. "v001".

    Returns
    -------
    processed : bool
        True if the instrument has at least one item on the day.
    """
    date = datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%d")
    response = table.query(
        KeyConditionExpression=Key("instrument_version").eq(f"{instrument}#{version}")
        & Key("time_utc").begins_with(date),
        Limit=1,
    )
    return response["Count"] > 0


def is_complete(day: str) -> bool:
    """Check if the day's packets pass the SSR coverage check.

    Parameters
    ----------
    day : str
        The day in YYYYMMDD format.

    Returns
    -------
    complete : bool
        True if the L0 files for the day and both neighbors exist and the
        day has no gaps.
    """
    day_datetime = datetime.strptime(day, "%Y%m%d")
    # Check the files exist before downloading kernels, since the day after
    # today is always a candidate and has no files yet.
    try:
        filenames = query_filenames(day_datetime)
    except FileNotFoundError as e:
        logger.info("%s not ready: %s", day, e)
        return False

    dependencies = get_latest_spice_kernels(
        os.environ["IMAP_DATA_ACCESS_URL"], day_datetime, TIME_KERNELS
    )
    spice_files = download_spice_files(dependencies, DOWNLOAD_DIR)
    with spiceypy.KernelPool([str(path) for path in spice_files]):
        try:
            combined = parse_packets(filenames, DOWNLOAD_DIR)
            check_day_coverage(combined, day_datetime)
        # parse_packets raises KeyError if a file has no I-ALiRT packets.
        except (KeyError, IalirtDataGapError) as e:
            logger.info("%s not ready: %s: %s", day, type(e).__name__, e)
            return False
    return True


def lambda_handler(event, context):
    """Invoke the instrument Lambdas asynchronously, once per instrument per day.

    Daily trigger: EventBridge schedule, empty detail.
    Manual trigger: custom EventBridge event with source "imap.ialirt.ssr",
    detail-type "SSR Processing Request" and detail
    {"start_date": "YYYYMMDD", "end_date": "YYYYMMDD", "version": "v001",
    "instruments": ["mag"]}. version and instruments are optional.

    Parameters
    ----------
    event : dict
        The EventBridge event.
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.
    """
    logger.info("Received event: %s", json.dumps(event))

    detail = event.get("detail", {})
    version = detail.get("version", DEFAULT_VERSION)
    if not re.fullmatch(r"v\d{3}", version):
        raise ValueError(f"version must look like 'v001', got {version!r}")
    instruments = detail.get("instruments", SSR_INSTRUMENTS)

    unknown = set(instruments) - set(SSR_INSTRUMENTS)
    if unknown:
        raise ValueError(f"Unknown instruments: {sorted(unknown)}")

    lambda_client = boto3.client("lambda")
    for day, day_instruments in get_days(detail, instruments).items():
        for instrument in day_instruments:
            payload = {"day": day, "version": version}
            lambda_client.invoke(
                FunctionName=function_name(instrument),
                InvocationType="Event",
                Payload=json.dumps(payload),
            )
            logger.info("Invoked %s with %s", function_name(instrument), payload)
