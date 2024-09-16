"""Test the IAlirt database."""

import pytest
from boto3.dynamodb.conditions import Key


@pytest.fixture()
def populate_table(table):
    """Populate DynamoDB table."""
    items = [
        {
            "ingest_date": 20210101,
            "met": 123,
            "packet_blob": b"binary_data_string",
        },
        {
            "ingest_date": 20210101,
            "met": 124,
            "packet_blob": b"binary_data_string",
        },
    ]
    for item in items:
        table.put_item(Item=item)

    return item


def test_query_by_sct_vtcw(table, populate_table):
    """Test to query irregular packet length."""
    response = table.query(KeyConditionExpression=Key("ingest_date").eq(20210101))

    items = response["Items"]
    assert items[0]["ingest_date"] == 20210101
    assert items[0]["met"] == 123
    assert items[1]["met"] == 124
