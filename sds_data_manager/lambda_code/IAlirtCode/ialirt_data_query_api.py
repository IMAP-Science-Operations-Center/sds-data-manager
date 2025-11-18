"""I-ALiRT Data Query lambda."""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

table_name = os.environ.get("DATA_TABLE")
region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(table_name)


def apply_time_filters(params: dict, query_kwargs: dict) -> Key:
    """Apply the filters for time.

    Parameters
    ----------
    params : dict
        Event parameters.
    query_kwargs : dict
        Query keyword arguments.

    Returns
    -------
    key_expr : Key
        The updated key expression with time filters applied.
    """
    key_expr = query_kwargs["KeyConditionExpression"]

    start = params.get("time_utc_start") or params.get("met_in_utc_start")
    end = params.get("time_utc_end") or params.get("met_in_utc_end")

    if start and end:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        if end_dt - start_dt > timedelta(days=1):
            return _error(400, "Start and end time cannot exceed 1 day apart.")
    elif start:
        # Calculate end to be 1 hour later.
        start_dt = datetime.fromisoformat(start)
        end_dt = start_dt + timedelta(hours=1)
        end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    elif end:
        # Calculate start to be 1 hour earlier.
        end_dt = datetime.fromisoformat(end)
        start_dt = end_dt - timedelta(hours=1)
        start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")

    key_expr &= Key("time_utc").between(start, end)
    query_kwargs["KeyConditionExpression"] = key_expr

    return key_expr


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
        "last_evaluated_key",
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

    # Pagination only allowed for one instrument
    if len(instruments) > 1 and params.get("last_evaluated_key"):
        return _error(400, "Pagination is only supported when querying one instrument")

    items = []
    query_time_total = 0

    for instrument in instruments:
        key_expr = Key("instrument").eq(instrument)
        query_kwargs = {"KeyConditionExpression": key_expr}

        if any(
            param in params
            for param in (
                "time_utc_start",
                "time_utc_end",
                "met_in_utc_start",
                "met_in_utc_end",
            )
        ):
            result = apply_time_filters(params, query_kwargs)
            # Checks if there was an error.
            if isinstance(result, dict):
                return result
        else:
            # Get latest 1 hour if not specified.
            logger.info(
                "No time range specified, defaulting to last 1 hour for instrument: %s",
                instrument,
            )
            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            query_kwargs["KeyConditionExpression"] &= Key("time_utc").between(
                one_hour_ago.isoformat(), now.isoformat()
            )

        if params.get("last_evaluated_key"):
            raw_last_evaluated_key = params["last_evaluated_key"]
            query_kwargs["ExclusiveStartKey"] = json.loads(
                unquote_plus(raw_last_evaluated_key)
            )

        t1 = time.perf_counter()
        response = table.query(**query_kwargs)
        t2 = time.perf_counter()
        items.extend(response.get("Items", []))
        query_time_total += t2 - t1

    last_evaluated_key = response.get("LastEvaluatedKey")

    t3 = time.perf_counter()

    encoded_lek = json.dumps(last_evaluated_key) if last_evaluated_key else None
    has_more = last_evaluated_key is not None

    json_body = json.dumps(
        {
            "meta": {
                "count": len(items),
                "type": meta_type,
                "instrument": meta_instrument,
                "has_more": has_more,
                "last_evaluated_key": encoded_lek,
            },
            "data": items,
        },
        default=str,
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
