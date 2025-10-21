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


@pytest.fixture
def populate_data_table(setup_data_table):
    """Populate DynamoDB table."""
    data_table = setup_data_table["data_table"]
    items = [
        {
            "instrument": "mag",
            "time_utc": "2021-01-01T00:00:00",
            "data_product_1": str(1234.56),
        },
        {
            "instrument": "mag",
            "time_utc": "2021-02-01T00:00:00",
            "data_product_2": str(101.3),
        },
    ]
    for item in items:
        data_table.put_item(Item=item)

    return items


def test_data_query_by_utc(setup_data_table, populate_data_table):
    """Test to query by met_in_utc."""
    data_table = setup_data_table["data_table"]
    expected_items = populate_data_table

    response = data_table.query(KeyConditionExpression=Key("instrument").eq("mag"))

    items = response["Items"]

    for item in range(len(items)):
        assert items[item] == expected_items[item]

    response = data_table.query(
        KeyConditionExpression=Key("instrument").eq("mag")
        & Key("time_utc").between("2021-00-00T00:00:00", "2021-01-02T00:00:00")
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["time_utc"] == expected_items[0]["time_utc"]


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
