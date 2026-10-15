"""Dispatch I-ALiRT SSR processing to the instrument Lambdas."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import boto3

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


def get_days(detail: dict) -> list[str]:
    """Return the days to process.

    Parameters
    ----------
    detail : dict
        The EventBridge event detail. Empty for the daily trigger, or
        {"start_date": "YYYYMMDD", "end_date": "YYYYMMDD"} (both inclusive)
        for the manual trigger.

    Returns
    -------
    days : list[str]
        Days in YYYYMMDD format.
    """
    if "start_date" in detail:
        start = datetime.strptime(detail["start_date"], "%Y%m%d")
        end = datetime.strptime(detail["end_date"], "%Y%m%d")
        if end < start:
            raise ValueError(f"end_date {end} is before start_date {start}")
    else:
        # Daily trigger. SSR packets arrive at DSN contacts (~2-3 per week),
        # so wait a week for the day and the start of the next day to be in
        # the archive.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start = end = now - timedelta(days=7)

    n_days = (end - start).days + 1
    return [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(n_days)]


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
    for day in get_days(detail):
        for instrument in instruments:
            payload = {"day": day, "version": version}
            lambda_client.invoke(
                FunctionName=function_name(instrument),
                InvocationType="Event",
                Payload=json.dumps(payload),
            )
            logger.info("Invoked %s with %s", function_name(instrument), payload)
