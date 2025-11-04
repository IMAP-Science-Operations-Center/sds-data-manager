"""I-ALiRT Database Query lambda."""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

table_name = os.environ.get("ALGORITHM_TABLE")
region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(table_name)

def apply_time_filters(params, key_expr, query_kwargs):
    time_prefixes = {"met_in_utc", "time_utc"}
    used_time_prefixes = {
        param.split("_start")[0].split("_end")[0]
        for param in params
        if any(param.startswith(prefix) for prefix in time_prefixes)
    }

    if len(used_time_prefixes) > 1:
        return _error(400, "Cannot query multiple time keys (met_in_utc, time_utc)")

    time_key = used_time_prefixes.pop()
    start = params.get(f"{time_key}_start")
    end = params.get(f"{time_key}_end")

    if start and end:
        key_expr &= Key(time_key).between(start, end)
    elif start:
        key_expr &= Key(time_key).gte(start)
    else:
        return _error(400, "End time provided without start time")

    query_kwargs["KeyConditionExpression"] = key_expr

    return


def _error(code, message):
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
    params = event.get("queryStringParameters", {})

    # --- Determine key condition ---
    allowed_params = {
        "instrument",
        "time_utc_start",
        "time_utc_end",
        "met_in_utc_start", # for backward compatibility
        "met_in_utc_end", # for backward compatibility
    }

    # Ensure allowed parameters
    unexpected = set(params) - allowed_params
    if unexpected:
        return _error(400, f"Unexpected parameters: {', '.join(unexpected)}")

    if not params.get("instrument"):
        logger.info("No instrument specified, defaulting to all instruments")

    # Get instrument or default to all.
    instruments = (
        [params["instrument"]] if params.get("instrument")
        else ["hit", "mag", "codice_lo", "codice_hi", "swapi", "swe"]
    )

    items = []

    for instrument in instruments:
        key_expr = Key("instrument").eq(instrument)
        query_kwargs = {"KeyConditionExpression": key_expr}

        if any(param.endswith("_start") or param.endswith("_end") for param in params):
            apply_time_filters(params, key_expr, query_kwargs)
        else:
            # Get latest 1 minute if not specified.
            logger.info("No time range specified, defaulting to last 1 minute for instrument: %s", instrument)
            now = datetime.now(timezone.utc)
            one_minute_ago = now - timedelta(minutes=1)
            key_expr &= Key("met_in_utc").between(one_minute_ago, now)
            query_kwargs["KeyConditionExpression"] &= Key("time_utc").between(
                one_minute_ago.isoformat(), now.isoformat()
            )
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "meta": {"count": len(items)},
            "data": items,
        })
    }
