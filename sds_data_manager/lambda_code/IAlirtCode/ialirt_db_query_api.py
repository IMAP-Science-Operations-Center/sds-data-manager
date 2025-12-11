"""I-ALiRT Database Query lambda."""

import json
import logging
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

table_name = os.environ.get("ALGORITHM_TABLE")
region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
dynamodb = boto3.resource("dynamodb", region_name=region)
table = dynamodb.Table(table_name)


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


def lambda_handler(event, context):  # noqa: PLR0912
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
    t1 = time.perf_counter()

    logger.info(f"Received event: {json.dumps(event)}")

    # --- Parse event ---
    params = event.get("queryStringParameters", {})

    if not params:
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "No query parameters provided"}),
        }

    key_expr = Key("apid").eq(478)
    query_kwargs = {"KeyConditionExpression": key_expr}
    t2 = time.perf_counter()

    # --- Determine key condition ---
    allowed_params = {
        "met_start",
        "met_end",
        "met_in_utc_start",
        "met_in_utc_end",
        "last_modified_start",
        "last_modified_end",
    }

    unexpected_params = set(params.keys()) - allowed_params
    if unexpected_params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": f"Unexpected parameters: {', '.join(unexpected_params)}"}
            ),
        }

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

    query_kwargs["KeyConditionExpression"] = key_expr
    t3 = time.perf_counter()

    # --- Query DynamoDB ---
    response = table.query(**query_kwargs)
    t4 = time.perf_counter()

    # --- Process items ---
    items = response.get("Items", [])
    t5 = time.perf_counter()

    # --- Serialize to JSON ---
    json_body = json.dumps(items, cls=DecimalEncoder)
    t6 = time.perf_counter()

    num_items = len(items)

    text = (
        f"Param parse: {t2 - t1:.3f}s | KeyCondition setup: {t3 - t2:.3f}s | "
        f"Query: {t4 - t3:.3f}s | Process: {t5 - t4:.3f}s | "
        f"JSON: {t6 - t5:.3f}s | TOTAL: {t6 - t1:.3f}s | "
        f"Items: {num_items}"
    )
    logger.info(text)

    return {"statusCode": 200, "body": json_body}
