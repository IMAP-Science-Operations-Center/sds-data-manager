"""I-ALiRT Database Query lambda."""

import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Attr, Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
    table_name = os.environ["ALGORITHM_TABLE"]
    region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    logger.info(f"Received event: {json.dumps(event)}")
    params = event.get("queryStringParameters", {})

    if not params:
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "No query parameters provided"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    key_expr = Key("apid").eq(478)
    query_kwargs = {"KeyConditionExpression": key_expr}

    allowed_params = {
        "met_start",
        "met_end",
        "insert_time_start",
        "insert_time_end",
        "product_name",
    }

    unexpected_params = set(params.keys()) - allowed_params
    if unexpected_params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": f"Unexpected parameters: {', '.join(unexpected_params)}"}
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if any(param.startswith("met") for param in params) and any(
        param.startswith("insert_time") for param in params
    ):
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": "Cannot query both MET and insert_time in the same request"}
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if ("met_start" in params and "met_end" in params) or (
        "insert_time_start" in params and "insert_time_end" in params
    ):
        time_key = "met" if "met_start" in params else "insert_time"

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

        if time_key == "insert_time":
            query_kwargs["IndexName"] = "insert_time"

    elif "met_start" in params or "insert_time_start" in params:
        time_key = "met" if "met_start" in params else "insert_time"

        start_value = (
            int(params[f"{time_key}_start"])
            if time_key == "met"
            else params[f"{time_key}_start"]
        )
        key_expr &= Key(time_key).gte(start_value)

        if time_key == "insert_time":
            query_kwargs["IndexName"] = "insert_time"

    elif "met_end" in params or "insert_time_end" in params:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": "Cannot query by end time without start time"}
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    query_kwargs["KeyConditionExpression"] = key_expr

    if "product_name" in params:
        product_name_value = params["product_name"]

        if product_name_value.endswith("*"):
            query_kwargs["FilterExpression"] = Attr("product_name").begins_with(
                product_name_value[:-1]
            )
        else:
            query_kwargs["FilterExpression"] = Attr("product_name").eq(
                product_name_value
            )

    response = table.query(**query_kwargs)
    return {
        "statusCode": 200,
        "body": json.dumps(response.get("Items", []), default=str),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    }
