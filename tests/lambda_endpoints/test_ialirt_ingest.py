"""Test the IAlirt ingest lambda function."""

import pytest
import boto3
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


def test_batch_get_met_range(table, populate_table):
    """Test querying a range of met values using BatchGetItem."""
    met_values = [123, 124]
    dynamodb = boto3.client("dynamodb")

    response = dynamodb.batch_get_item(
        RequestItems={
            table.table_name: {
                "Keys": [{"met": {"N": str(met)}} for met in met_values]
            }
        }
    )

    items = response.get("Responses", {}).get(table.table_name, [])

    assert int(items[0]["met"]["N"]) == met_values[0]
    assert int(items[1]["met"]["N"]) == met_values[1]
    assert items[0]["packet_blob"]["B"] == b"binary_data_string"
    assert items[1]["packet_blob"]["B"] == b"binary_data_string"


def test_query_ingest_time_range(table, populate_table):
    """Test querying a range of ingest_time values using the GSI."""

    response = table.query(
        IndexName="ingest_time",
        KeyConditionExpression=Key("ingest_time").eq("2021-01-01T00:00:01Z"),
    )
    item = response.get("Items")

    response = table.query(
        IndexName="ingest_time",
        KeyConditionExpression=Key("ingest_time").between("2021-01-01T00:00:00Z", "2021-01-01T00:00:02Z")
    )

    items = response.get("Items", [])

    # Assert the data is correct
    assert items[0]["ingest_time"] == "2021-01-01T00:00:00Z"
    assert items[1]["ingest_time"] == "2021-01-01T00:00:01Z"

    assert items[0]["packet_blob"] == b"binary_data_string"
    assert items[1]["packet_blob"] == b"binary_data_string"
