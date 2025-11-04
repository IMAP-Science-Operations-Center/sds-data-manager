"""I-ALiRT Database Query lambda."""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

table_name = os.environ.get("ALGORITHM_TABLE")
region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(table_name)

def apply_time_filters(params, query_kwargs):
    key_expr = query_kwargs["KeyConditionExpression"]

    start = params.get("time_utc_start") or params.get("met_in_utc_start")
    end = params.get("time_utc_end") or params.get("met_in_utc_end")

    if start and end:
        key_expr &= Key("time_utc").between(start, end)
    elif start:
        key_expr &= Key("time_utc").gte(start)
    else:
        return _error(400, "End time provided without start time")

    query_kwargs["KeyConditionExpression"] = key_expr

    return key_expr


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
    last_evaluated = []
    query_time_total = 0

    for instrument in instruments:
        key_expr = Key("instrument").eq(instrument)
        query_kwargs = {"KeyConditionExpression": key_expr}

        if any(param in params for param in ("time_utc_start", "time_utc_end",
                                     "met_in_utc_start", "met_in_utc_end")):
            result = apply_time_filters(params, query_kwargs)
            # Checks if there was an error.
            if isinstance(result, dict):
                return result
        else:
            # Get latest 1 minute if not specified.
            logger.info("No time range specified, defaulting to last 1 minute for instrument: %s", instrument)
            now = datetime.now(timezone.utc)
            one_minute_ago = now - timedelta(minutes=1)
            query_kwargs["KeyConditionExpression"] &= Key("time_utc").between(
                one_minute_ago.isoformat(), now.isoformat()
            )
        t1 = time.perf_counter()
        response = table.query(**query_kwargs)
        t2 = time.perf_counter()
        items.extend(response.get("Items", []))
        query_time_total += (t2 - t1)
        last_evaluated_key = response.get("LastEvaluatedKey")

    t3 = time.perf_counter()
    json_body = json.dumps({
            "meta": {"count": len(items)},
            "data": items,
        })
    t4 = time.perf_counter()

    logger.info(
        f"Query total: {query_time_total:.3f}s | "
        f"JSON build: {t4 - t3:.3f}s | "
        f"Items: {len(items)}"
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json_body
    }
