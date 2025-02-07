import os
import json
import boto3
from boto3.dynamodb.conditions import Key, Attr


def lambda_handler(event, context):
    table_name = os.environ["TABLE_NAME"]
    region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    params = event["queryStringParameters"]
    apid = int(params["apid"])
    met_start = int(params["met_start"])
    met_end = int(params["met_end"])

    key_expr = Key("apid").eq(apid) & Key("met").between(met_start, met_end)
    query_kwargs = {"KeyConditionExpression": key_expr}

    if "product_name" in params:
        query_kwargs["FilterExpression"] = Attr("product_name").eq(params["product_name"])

    response = table.query(**query_kwargs)
    items = response.get("Items", [])

    return {
        "statusCode": 200,
        "body": json.dumps(items),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        }
    }
