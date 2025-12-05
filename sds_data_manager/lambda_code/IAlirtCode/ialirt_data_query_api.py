"""I-ALiRT Data Query lambda."""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

table_name = os.environ.get("DATA_TABLE")
region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(table_name)


class BadTimeError(Exception):
    """Raised when the time filters are invalid."""

    pass


class DecimalEncoder(json.JSONEncoder):
    """Convert Decimals to floats."""

    def default(self, obj):
        """Override JSON encoding for Decimal values."""
        if isinstance(obj, Decimal):
            # - If the Decimal is an integer, return int
            # - Otherwise, float rounded to 3 decimal places
            if obj % 1 == 0:
                return int(obj)
            return round(float(obj), 3)

        # Let the base class raise for other unsupported types
        return super().default(obj)


def apply_time_filters(params: dict, query_kwargs: dict) -> tuple:
    """Apply the filters for time.

    Parameters
    ----------
    params : dict
        Event parameters.
    query_kwargs : dict
        Query keyword arguments.

    Returns
    -------
    query_kwargs : dict
        The updated key expression with time filters applied.
    start : str
        Start time.
    end : str
        End time.
    """
    key_expr = query_kwargs["KeyConditionExpression"]

    start = params.get("time_utc_start") or params.get("met_in_utc_start")
    end = params.get("time_utc_end") or params.get("met_in_utc_end")

    if start and end:
        start_dt = validate_time(start)
        end_dt = validate_time(end)
    elif start:
        start_dt = validate_time(start)
        end_dt = start_dt + timedelta(hours=1)
    elif end:
        end_dt = validate_time(end)
        start_dt = end_dt - timedelta(hours=1)
    else:
        # Get latest 1 hour if not specified.
        logger.info("No time range specified, defaulting to last 1 hour.")
        now = datetime.now(timezone.utc)
        start_dt = now - timedelta(hours=1)
        end_dt = now

    start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

    key_expr &= Key("time_utc").between(start, end)
    query_kwargs["KeyConditionExpression"] = key_expr

    return query_kwargs, start, end


def validate_time(ts: str):
    """Validate a timestamp string in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).

    Parameters
    ----------
    ts : str
        The timestamp string to validate.

    Returns
    -------
    datetime | None
        A `datetime` object if the timestamp is valid,
        or `None` if the format is invalid.
    """
    try:
        return datetime.fromisoformat(ts)
    except Exception as e:
        raise BadTimeError("Invalid time format") from e


def _error(code: int, message: str) -> dict:
    """Create error dictionary.

    Parameters
    ----------
    code : int
        Error code.
    message : str
        The error message.

    Returns
    -------
    error : dict
        The error dictionary.
    """
    return {
        "statusCode": code,
        "body": json.dumps({"message": message}),
        "headers": {"Content-Type": "application/json"},
    }


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
    params = event.get("queryStringParameters") or {}

    # --- Determine key condition ---
    allowed_params = {
        "instrument",
        "time_utc_start",
        "time_utc_end",
        "met_in_utc_start",  # for backward compatibility
        "met_in_utc_end",  # for backward compatibility
    }

    # Ensure allowed parameters
    unexpected = set(params) - allowed_params
    if unexpected:
        return _error(400, f"Unexpected parameters: {', '.join(unexpected)}")

    if not params.get("instrument"):
        meta_instrument = "all"
        meta_type = "science"
    elif params["instrument"] == "spice":
        meta_instrument = "spice"
        meta_type = "spice"
    elif params["instrument"].endswith("hk"):
        meta_instrument = params["instrument"]
        meta_type = "hk"
    elif params["instrument"] == "spacecraft":
        meta_instrument = params["instrument"]
        meta_type = "spacecraft"
    else:
        meta_instrument = params["instrument"]
        meta_type = "science"

    # Get instrument or default to all.
    requested_instrument = params.get("instrument")
    instruments = (
        [requested_instrument]
        if requested_instrument
        else ["hit", "mag", "codice_lo", "codice_hi", "swapi", "swe"]
    )

    items = []
    query_time_total = 0

    for instrument in instruments:
        key_expr = Key("instrument").eq(instrument)
        query_kwargs = {"KeyConditionExpression": key_expr}

        try:
            query_kwargs, range_start, range_end = apply_time_filters(
                params, query_kwargs
            )
        except BadTimeError as e:
            return _error(400, str(e))

        t1 = time.perf_counter()
        response = table.query(**query_kwargs)

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
            f"Querying {instrument} between {range_start} and "
            f"{range_end} took {t2 - t1} s"
        )
        raw_items = response.get("Items", [])
        items.extend(raw_items)
        query_time_total += t2 - t1

    t3 = time.perf_counter()

    json_body = json.dumps(
        {
            "meta": {
                "count": len(items),
                "type": meta_type,
                "instrument": meta_instrument,
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
