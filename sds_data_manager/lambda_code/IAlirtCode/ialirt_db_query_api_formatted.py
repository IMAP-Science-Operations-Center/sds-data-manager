"""I-ALiRT Database Query lambda."""

import json
import logging
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def process_item_types(item: dict) -> dict:
    """Convert Decimal values to int/float for known fields.

    Parameters
    ----------
    item : dict
        The item in the dictionary.

    Returns
    -------
    result : dict
        Properly formatted parameters.

    Note: Truncates to 3 decimal places to reduce response size.
    """
    result = {}

    for key, value in item.items():
        # Vectors fields
        if isinstance(value, list):
            result[key] = [int(v) if v % 1 == 0 else round(float(v), 3) for v in value]

        # Dictionary with number
        elif isinstance(value, dict) and "N" in value:
            num = Decimal(value["N"])
            result[key] = int(num) if num % 1 == 0 else round(float(num), 3)

        elif isinstance(value, dict) and "BOOL" in value:
            result[key] = bool(value["BOOL"])

        # Scalar fields
        elif isinstance(value, Decimal):
            result[key] = int(value) if value % 1 == 0 else round(float(value), 3)

        else:
            result[key] = value

    return result


def lambda_handler(event, context):  # noqa: PLR0912, PLR0915
    """Read and format database query.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    Example
    -------
    result = {'hit_he_omni_high_en': [0, None],
    'mag_B_GSE': [[-6.382, -1.353, -5.045],
    [-2.058, 3.792, -3.989]],
    'time_tag': ['2025-10-02T07:07:13', '2025-10-02T07:07:17'], ...}
    """
    table_name = os.environ.get("ALGORITHM_TABLE")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    logger.info(f"Received event: {json.dumps(event)}")
    params = event.get("queryStringParameters", {})

    if not params:
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "No query parameters provided"}),
        }

    key_expr = Key("apid").eq(478)
    query_kwargs = {"KeyConditionExpression": key_expr}

    allowed_params = {
        "start_time",
        "end_time",
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

    response = table.query(**query_kwargs)

    items = response.get("Items", [])
    processed_items = [process_item_types(item) for item in items]

    if processed_items:
        keys = processed_items[0].keys()
        result = {
            key: [item.get(key) for item in processed_items]
            for key in keys
            if key not in ("met", "ttj2000ns", "apid", "last_modified")
        }
        if "met_in_utc" in result:
            result["time_tag"] = result.pop("met_in_utc")
    else:
        result = {}

    return {"statusCode": 200, "body": json.dumps(result)}
