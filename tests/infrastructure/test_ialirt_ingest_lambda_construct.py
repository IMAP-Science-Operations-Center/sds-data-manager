"""Test the IAlirt database."""

import pytest
from boto3.dynamodb.conditions import Key


@pytest.fixture()
def populate_ingest_table(setup_dynamodb):
    """Populate DynamoDB table."""
    ingest_table = setup_dynamodb["ingest_table"]
    items = [
        {
            "apid": 478,
            "met": 123,
            "ingest_time": "2021-01-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
        {
            "apid": 478,
            "met": 124,
            "ingest_time": "2021-02-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
    ]
    for item in items:
        ingest_table.put_item(Item=item)

    return items


@pytest.fixture()
def populate_algorithm_table(setup_dynamodb):
    """Populate DynamoDB table."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    items = [
        {
            "product_name": "hit_product_1",
            "met": 123,
            "insert_time": "2021-01-01T00:00:00Z",
            "data_product_1": str(1234.56),
        },
        {
            "product_name": "hit_product_1",
            "met": 124,
            "insert_time": "2021-02-01T00:00:00Z",
            "data_product_2": str(101.3),
        },
    ]
    for item in items:
        algorithm_table.put_item(Item=item)

    return items


def test_ingest_query_by_met(setup_dynamodb, populate_ingest_table):
    """Test to query by met."""
    ingest_table = setup_dynamodb["ingest_table"]
    expected_items = populate_ingest_table

    response = ingest_table.query(KeyConditionExpression=Key("apid").eq(478))

    items = response["Items"]

    for item in range(len(items)):
        assert items[item] == expected_items[item]

    response = ingest_table.query(
        KeyConditionExpression=Key("apid").eq(478) & Key("met").between(100, 123)
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == expected_items[0]["met"]


def test_ingest_query_by_date(setup_dynamodb, populate_ingest_table):
    """Test to query by date."""
    ingest_table = setup_dynamodb["ingest_table"]
    expected_items = populate_ingest_table

    response = ingest_table.query(
        IndexName="ingest_time",
        KeyConditionExpression=Key("apid").eq(478)
        & Key("ingest_time").begins_with("2021-01"),
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0] == expected_items[0]


def test_algorithm_query_by_met(setup_dynamodb, populate_algorithm_table):
    """Test to query by met."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    expected_items = populate_algorithm_table

    response = algorithm_table.query(
        KeyConditionExpression=Key("product_name").eq("hit_product_1")
    )

    items = response["Items"]

    for item in range(len(items)):
        assert items[item] == expected_items[item]

    response = algorithm_table.query(
        KeyConditionExpression=Key("product_name").eq("hit_product_1")
        & Key("met").between(100, 123)
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == expected_items[0]["met"]


def test_algorithm_query_by_date(setup_dynamodb, populate_algorithm_table):
    """Test to query by date."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    expected_items = populate_algorithm_table

    response = algorithm_table.query(
        IndexName="insert_time",
        KeyConditionExpression=Key("product_name").eq("hit_product_1")
        & Key("insert_time").begins_with("2021-01"),
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0] == expected_items[0]
