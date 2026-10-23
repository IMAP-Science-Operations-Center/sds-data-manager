"""I-ALiRT SSR Data Query lambda."""

import json
import logging
import os
import time
from datetime import datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key

from .ialirt_query_utils import (
    FULL_SCOPES,
    PUBLIC_CUTOFF_UTC,
    BadTimeError,
    DecimalEncoder,
    _error,
    filter_items_by_scope,
    validate_time,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

table_name = os.environ.get("SSR_TABLE")
region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(table_name)

DEFAULT_VERSION = "v001"

SCIENCE_INSTRUMENTS = ["hit", "mag", "codice_lo", "codice_hi", "swapi", "swe"]

# Instruments stored in the SSR table.
SSR_INSTRUMENTS = {
    *SCIENCE_INSTRUMENTS,
    "spacecraft_status",
    "spacecraft",
    "spice",
    "mag_hk",
    # Like ialirt-data-query, ancillary has no branch of its own and is
    # returned with type "science".
    "ancillary",
}


def latest_time_utc(instrument_version: str) -> str | None:
    """Return the latest time_utc stored for an instrument and version.

    Parameters
    ----------
    instrument_version : str
        The partition key, e.g. "mag#v001".

    Returns
    -------
    str | None
        The latest time_utc, or None if there is no data.
    """
    response = table.query(
        KeyConditionExpression=Key("instrument_version").eq(instrument_version),
        ScanIndexForward=False,
        Limit=1,
        ProjectionExpression="time_utc",
    )
    items = response.get("Items", [])
    return items[0]["time_utc"] if items else None


def format_time(dt: datetime, microseconds: bool) -> str:
    """Format a time like time_utc.

    spice and ancillary items have time_utc = kernel_set_key, which always has
    microseconds, even when they are zero (e.g. 2026-10-20T06:00:01.000000).
    So microseconds are kept whenever the given time had them, not only when
    they are non-zero.

    Parameters
    ----------
    dt : datetime
        The time to format.
    microseconds : bool
        Whether to include microseconds.

    Returns
    -------
    str
        The time as YYYY-MM-DDTHH:MM:SS, plus .ffffff if microseconds is True.
    """
    if microseconds:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def get_time_range(
    params: dict, instrument_version: str, has_api_key: bool
) -> tuple[str, str] | None:
    """Get the time range to query.

    Parameters
    ----------
    params : dict
        Event parameters.
    instrument_version : str
        The partition key, e.g. "mag#v001".
    has_api_key : bool
        Whether or not user used api key.

    Returns
    -------
    tuple[str, str] | None
        Start and end time, or None if no time range was given and there is
        no data for instrument_version.
    """
    start = params.get("time_utc_start")
    end = params.get("time_utc_end")

    # A "." means the time has fractional seconds.
    if start and end:
        start = format_time(validate_time(start), "." in start)
        end = format_time(validate_time(end), "." in end)
    elif start:
        start_dt = validate_time(start)
        microseconds = "." in start
        start = format_time(start_dt, microseconds)
        end = format_time(start_dt + timedelta(hours=1), microseconds)
    elif end:
        end_dt = validate_time(end)
        microseconds = "." in end
        start = format_time(end_dt - timedelta(hours=1), microseconds)
        end = format_time(end_dt, microseconds)
    else:
        # SSR data lags by at least a day, so default to the latest
        # 1 hour of data instead of the last hour before now.
        logger.info("No time range specified, defaulting to latest 1 hour of data.")
        end = latest_time_utc(instrument_version)
        if end is None:
            return None
        # Keep end as stored so the latest item is included even if
        # time_utc has fractional seconds.
        start = format_time(validate_time(end) - timedelta(hours=1), "." in end)

    if not has_api_key and start < PUBLIC_CUTOFF_UTC:
        raise BadTimeError("API key required for data prior to 2026-02-01T00:00:00")

    return start, end


def lambda_handler(event, context):  # noqa: PLR0911, PLR0912, PLR0915
    """Query the I-ALiRT SSR table.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Notes
    -----
    All parameters are optional. Defaults to the latest 1 hour of version
    v001 data for each science instrument.

    Example
    -------
    {
        "queryStringParameters": {
            "instrument": "mag",
            "version": "v001",
            "time_utc_start": "2026-09-01T00:00:00",
            "time_utc_end": "2026-09-01T01:00:00"
        }
    }
    """
    params = event.get("queryStringParameters") or {}

    # If there is an api-key used, retrieve the scope.
    request_ctx = event.get("requestContext", {})
    auth = request_ctx.get("authorizer", {})
    auth_ctx = auth.get("lambda", {})
    scope = auth_ctx.get("scope", "")
    has_api_key = bool(auth)

    allowed_params = {
        "instrument",
        "version",
        "time_utc_start",
        "time_utc_end",
    }

    # Ensure allowed parameters
    unexpected = set(params) - allowed_params
    if unexpected:
        return _error(400, f"Unexpected parameters: {', '.join(unexpected)}")

    version = params.get("version", DEFAULT_VERSION)

    requested_instrument = params.get("instrument")
    if not requested_instrument:
        meta_instrument = "all"
        meta_type = "science"
    elif requested_instrument not in SSR_INSTRUMENTS:
        return _error(400, f"Unsupported instrument: {requested_instrument}")
    elif requested_instrument.endswith("hk"):
        if scope not in FULL_SCOPES:
            return _error(403, "Unauthorized for HK access.")
        meta_instrument = requested_instrument
        meta_type = "hk"
    elif requested_instrument == "spice":
        meta_instrument = "spice"
        meta_type = "spice"
    elif requested_instrument == "spacecraft_status":
        if not has_api_key:
            return _error(403, "Authentication required for spacecraft_status access.")
        meta_instrument = requested_instrument
        meta_type = "spacecraft_status"
    elif requested_instrument == "spacecraft":
        meta_instrument = requested_instrument
        meta_type = "spacecraft"
    else:
        meta_instrument = requested_instrument
        meta_type = "science"

    # Get instrument or default to all.
    instruments = (
        [requested_instrument] if requested_instrument else SCIENCE_INSTRUMENTS
    )

    items = []
    query_time_total = 0

    for instrument in instruments:
        instrument_version = f"{instrument}#{version}"

        try:
            time_range = get_time_range(params, instrument_version, has_api_key)
        except BadTimeError as e:
            return _error(400, str(e))

        if time_range is None:
            logger.info(f"No data for {instrument_version}.")
            continue

        range_start, range_end = time_range
        key_expr = Key("instrument_version").eq(instrument_version) & Key(
            "time_utc"
        ).between(range_start, range_end)

        t1 = time.perf_counter()
        response = table.query(KeyConditionExpression=key_expr)

        if "LastEvaluatedKey" in response:
            return _error(
                400,
                (
                    f"Your request for '{instrument}' returned more data than allowed "
                    f"in a single query. Please reduce the time window "
                    f"or filter further."
                ),
            )

        t2 = time.perf_counter()
        logger.info(
            f"Querying {instrument_version} between {range_start} and "
            f"{range_end} took {t2 - t1} s"
        )
        raw_items = response.get("Items", [])
        if instrument == "hit":
            raw_items = filter_items_by_scope(raw_items, scope)
        items.extend(raw_items)
        query_time_total += t2 - t1

    t3 = time.perf_counter()

    json_body = json.dumps(
        {
            "meta": {
                "count": len(items),
                "type": meta_type,
                "instrument": meta_instrument,
                "version": version,
            },
            "data": items,
        },
        cls=DecimalEncoder,
    )

    t4 = time.perf_counter()

    logger.info(
        f"Query total: {query_time_total:.3f}s | "
        f"JSON build: {t4 - t3:.3f}s | "
        f"Items: {len(items)}"
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json_body,
    }
