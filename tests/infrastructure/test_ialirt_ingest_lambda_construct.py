"""Test the IAlirt database."""

import pytest
from boto3.dynamodb.conditions import Key


@pytest.fixture()
def populate_table(table):
    """Populate DynamoDB table."""
    items = [
        {
            "apid": 478,
            "met": 123,
            "ingest_date": "2021-01-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
        {
            "apid": 478,
            "met": 124,
            "ingest_date": "2021-02-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
    ]
    for item in items:
        table.put_item(Item=item)

    return items


def test_query_by_met(table, populate_table):
    """Test to query by met."""
    expected_items = populate_table

    response = table.query(KeyConditionExpression=Key("apid").eq(478))

    items = response["Items"]

    for item in range(len(items)):
        assert items[item]["apid"] == expected_items[item]["apid"]
        assert items[item]["met"] == expected_items[item]["met"]
        assert items[item]["ingest_date"] == expected_items[item]["ingest_date"]
        assert items[item]["packet_blob"] == expected_items[item]["packet_blob"]

    response = table.query(
        KeyConditionExpression=Key("apid").eq(478) & Key("met").between(100, 123)
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == expected_items[0]["met"]


def test_query_by_date(table, populate_table):
    """Test to query by date."""
    expected_items = populate_table

    response = table.query(
        IndexName="ingest_date",
        KeyConditionExpression=Key("apid").eq(478)
        & Key("ingest_date").begins_with("2021-01"),
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == expected_items[0]["met"]
