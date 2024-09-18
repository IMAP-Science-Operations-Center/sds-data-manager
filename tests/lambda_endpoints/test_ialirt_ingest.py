"""Test the IAlirt ingest lambda function."""

import pytest
from boto3.dynamodb.conditions import Key

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import lambda_handler


@pytest.fixture()
def populate_table(table):
    """Populate DynamoDB table."""
    items = [
        {
            "met": 123,
            "ingest_time": "2021-01-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
        {
            "met": 124,
            "ingest_time": "2021-01-01T00:00:01Z",
            "packet_blob": b"binary_data_string",
        },
    ]
    for item in items:
        table.put_item(Item=item)

    return item


def test_lambda_handler(table):
    """Test the lambda_handler function."""
    # Mock event data
    event = {"detail": {"object": {"key": "packets/file.txt"}}}

    lambda_handler(event, {})

    response = table.get_item(
        Key={
            "met": 123,
            "ingest_time": "2021-01-01T00:00:00Z",
        }
    )
    item = response.get("Item")

    assert item is not None
    assert item["met"] == 123
    assert item["packet_blob"] == b"binary_data_string"


def test_query_by_met(table, populate_table):
    """Test to query irregular packet length."""
    response = table.query(KeyConditionExpression=Key("met").eq(124))

    items = response["Items"]
    assert items[0]["met"] == 124
