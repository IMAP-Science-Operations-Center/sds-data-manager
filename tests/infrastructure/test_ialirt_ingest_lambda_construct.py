"""Test the IAlirt database."""

import pytest
from boto3.dynamodb.conditions import Key


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


def test_query_by_sct_vtcw(table, populate_table):
    """Test to query irregular packet length."""
    response = table.query(KeyConditionExpression=Key("met").eq(124))

    items = response["Items"]
    assert items[0]["met"] == 124
    assert items[0]["ingest_time"] == "2021-01-01T00:00:01Z"
