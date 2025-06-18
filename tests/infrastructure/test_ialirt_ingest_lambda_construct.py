"""Test the IAlirt database."""

import pytest
from boto3.dynamodb.conditions import Key


@pytest.fixture
def populate_algorithm_table(setup_dynamodb):
    """Populate DynamoDB table."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    items = [
        {
            "apid": 478,
            "met": 123,
            "met_in_utc": "2021-01-01T00:00:00",
            "last_modified": "2021-01-01T00:00:00",
            "data_product_1": str(1234.56),
        },
        {
            "apid": 478,
            "met": 124,
            "met_in_utc": "2021-02-01T00:00:00",
            "last_modified": "2021-02-01T00:00:00",
            "data_product_2": str(101.3),
        },
    ]
    for item in items:
        algorithm_table.put_item(Item=item)

    return items


def test_algorithm_query_by_met(setup_dynamodb, populate_algorithm_table):
    """Test to query by met."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    expected_items = populate_algorithm_table

    response = algorithm_table.query(KeyConditionExpression=Key("apid").eq(478))

    items = response["Items"]

    for item in range(len(items)):
        assert items[item] == expected_items[item]

    response = algorithm_table.query(
        KeyConditionExpression=Key("apid").eq(478) & Key("met").between(100, 123)
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == expected_items[0]["met"]


def test_algorithm_query_by_date(setup_dynamodb, populate_algorithm_table):
    """Test to query by date."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    expected_items = populate_algorithm_table

    response = algorithm_table.query(
        IndexName="met_in_utc",
        KeyConditionExpression=Key("apid").eq(478)
        & Key("met_in_utc").begins_with("2021-01"),
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0] == expected_items[0]


def test_algorithm_query_by_last_modified(setup_dynamodb, populate_algorithm_table):
    """Test to query by last_modified."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    expected_items = populate_algorithm_table

    response = algorithm_table.query(
        IndexName="last_modified",
        KeyConditionExpression=Key("apid").eq(478)
        & Key("last_modified").begins_with("2021-01"),
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0] == expected_items[0]
