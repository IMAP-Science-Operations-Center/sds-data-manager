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

def query_times(params, key_expr, query_kwargs):
    time_prefixes = {"met", "met_in_utc", "last_modified"}
    used_time_prefixes = {
        param.split("_start")[0].split("_end")[0]
        for param in params
        if any(param.startswith(prefix) for prefix in time_prefixes)
    }

    if len(used_time_prefixes) > 1:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "message": "Cannot query multiple time keys "
                               "(met, met_in_utc, last_modified)"
                }
            ),
        }

    if (
            ("met_start" in params and "met_end" in params)
            or ("met_in_utc_start" in params and "met_in_utc_end" in params)
            or ("last_modified_start" in params and "last_modified_end" in params)
    ):
        if "met_start" in params:
            time_key = "met"
        elif "met_in_utc_start" in params:
            time_key = "met_in_utc"
        else:
            time_key = "last_modified"

        start_value = (
            int(params[f"{time_key}_start"])
            if time_key == "met"
            else params[f"{time_key}_start"]
        )
        end_value = (
            int(params[f"{time_key}_end"])
            if time_key == "met"
            else params[f"{time_key}_end"]
        )

        key_expr &= Key(time_key).between(start_value, end_value)

        if time_key in {"met_in_utc", "last_modified"}:
            query_kwargs["IndexName"] = time_key

    elif (
            "met_start" in params
            or "met_in_utc_start" in params
            or "last_modified_start" in params
    ):
        if "met_start" in params:
            time_key = "met"
        elif "met_in_utc_start" in params:
            time_key = "met_in_utc"
        else:
            time_key = "last_modified"

        start_value = (
            int(params[f"{time_key}_start"])
            if time_key == "met"
            else params[f"{time_key}_start"]
        )
        key_expr &= Key(time_key).gte(start_value)

        if time_key in {"met_in_utc", "last_modified"}:
            query_kwargs["IndexName"] = time_key

    elif (
            "met_end" in params
            or "met_in_utc_end" in params
            or "last_modified_end" in params
    ):
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": "Cannot query by end time without start time"}
            ),
        }

    return key_expr


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

    # --- Parse event ---
    params = event.get("queryStringParameters", {})

    if not params:
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "No query parameters provided."}),
        }

    # --- Determine key condition ---
    allowed_params = {
        "instrument",
        "time_utc_start",
        "time_utc_end",
        "met_in_utc_start", # To keep continuity with other API.
        "met_in_utc_end", # To keep continuity with other API.
    }

    unexpected_params = set(params.keys()) - allowed_params
    if unexpected_params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": f"Unexpected parameters: {', '.join(unexpected_params)}"}
            ),
        }

    if not params.get("instrument"):
        instruments = ["hit", "mag", "codice_lo", "codice_hi", "swapi", "swe"]
    else:
        instruments = [params["instrument"]]

    for instrument in instruments:
        key_expr = Key("instrument").eq(instrument)
        query_kwargs = {"KeyConditionExpression": key_expr}

        if params:
            key_expr = query_times(params, key_expr, query_kwargs)
            response = table.query(**query_kwargs)
        else:
            now = datetime.now(timezone.utc)
            one_minute_ago = now - timedelta(minutes=1)
            key_expr &= Key("met_in_utc").between(one_minute_ago, now)

