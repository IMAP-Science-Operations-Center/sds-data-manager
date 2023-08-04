import os

import boto3
from boto3.dynamodb.conditions import Attr, Key


def handler(event, context):
    """Dummy code that checks if DynamoDB table has any
    status == PENDING for given input instrument.

    Parameters
    ----------
    event : Dict
    context : LambdaContext

    Returns
    -------
    Dict
        status_code: 200 if there are pending data, 204 otherwise
    """
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

    # Read Dynamodb table to get all data for given instrument
    # with status == 'PENDING'
    instrument = event["instrument"]
    query_response = table.query(
        KeyConditionExpression=Key("instrument").eq(instrument),
        FilterExpression=Attr("status").eq("PENDING"),
    )

    if query_response["Count"] == 0:
        print("No data to process")
        return {"status_code": 204}
    elif query_response["Count"] > 0:
        print("Data to process")
        return {"status_code": 200}
    print(query_response)
    return {"status_code": 404}
