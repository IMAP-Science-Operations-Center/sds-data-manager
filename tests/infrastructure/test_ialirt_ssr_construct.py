"""Test the IAlirt SSR database."""

import pytest
from boto3.dynamodb.conditions import Attr, Key


@pytest.fixture
def populate_ssr_table(setup_ssr_table):
    """Populate DynamoDB table."""
    ssr_table = setup_ssr_table["ssr_table"]
    items = [
        {
            "instrument": "mag",
            "time_utc": "2021-01-01T00:00:00",
            "version": "v001",
            "data_product_1": str(1234.56),
        },
        {
            "instrument": "mag",
            "time_utc": "2021-01-01T00:01:00",
            "version": "v002",
            "data_product_1": str(1234.57),
        },
        {
            "instrument": "mag",
            "time_utc": "2021-02-01T00:00:00",
            "version": "v002",
            "data_product_2": str(101.3),
        },
    ]
    for item in items:
        ssr_table.put_item(Item=item)

    return items


def test_ssr_query_by_utc_and_version(setup_ssr_table, populate_ssr_table):
    """Test to query by time_utc and filter by version."""
    ssr_table = setup_ssr_table["ssr_table"]
    expected_items = populate_ssr_table

    response = ssr_table.query(
        KeyConditionExpression=Key("instrument").eq("mag")
        & Key("time_utc").between("2021-01-01T00:00:00", "2021-01-02T00:00:00"),
        FilterExpression=Attr("version").eq("v002"),
    )
    items = response["Items"]
    assert len(items) == 1
    assert items[0] == expected_items[1]
