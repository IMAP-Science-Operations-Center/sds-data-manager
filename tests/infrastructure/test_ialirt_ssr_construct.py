"""Test the IAlirt SSR database."""

import pytest
from boto3.dynamodb.conditions import Key


def make_item(version, time_utc, value):
    """Create an SSR table item with its key attributes."""
    return {
        "instrument_version": f"mag#{version}",
        "time_utc": time_utc,
        "instrument": "mag",
        "version": version,
        "data_product_1": str(value),
    }


@pytest.fixture
def populate_ssr_table(setup_ssr_table):
    """Populate DynamoDB table."""
    ssr_table = setup_ssr_table["ssr_table"]
    # v001 and v002 both have data at the first time.
    items = [
        make_item("v001", "2021-01-01T00:00:00", 1234.56),
        make_item("v001", "2021-01-01T00:01:00", 1234.57),
        make_item("v002", "2021-01-01T00:00:00", 1234.58),
        make_item("v002", "2021-02-01T00:00:00", 101.3),
    ]
    for item in items:
        ssr_table.put_item(Item=item)

    return items


@pytest.mark.parametrize(
    ("version", "expected_indices"), [("v001", [0, 1]), ("v002", [2])]
)
def test_ssr_query_by_version_and_utc(
    setup_ssr_table, populate_ssr_table, version, expected_indices
):
    """Test that each version is kept and can be queried by time_utc."""
    ssr_table = setup_ssr_table["ssr_table"]
    expected_items = populate_ssr_table

    response = ssr_table.query(
        KeyConditionExpression=Key("instrument_version").eq(f"mag#{version}")
        & Key("time_utc").between("2021-01-01T00:00:00", "2021-01-02T00:00:00"),
    )
    items = response["Items"]
    assert items == [expected_items[i] for i in expected_indices]
