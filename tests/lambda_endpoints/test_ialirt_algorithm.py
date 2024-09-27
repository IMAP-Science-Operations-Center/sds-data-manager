"""Test the IAlirt algorithm lambda function."""

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import lambda_handler


@pytest.fixture()
def populate_table(algorithm_table):
    """Populate DynamoDB table."""
    items = [
        {
            "instrument": "hit",
            "met": 123,
            "insert_time": "2021-01-01T00:00:00Z",
            "data_product_1": str(1234.56),
        },
        {
            "instrument": "hit",
            "met": 124,
            "insert_time": "2021-02-01T00:00:00Z",
            "data_product_2": str(101.3),
        },
    ]
    for item in items:
        algorithm_table.put_item(Item=item)

    return items


def test_lambda_handler(algorithm_table):
    """Test the lambda_handler function."""
    # Mock event data
    event = {"detail": {"object": {"key": "packets/file.txt"}}}

    lambda_handler(event, {})

    response = algorithm_table.get_item(
        Key={
            "instrument": "hit",
            "met": 123,
        }
    )
    item = response.get("Item")

    assert item is not None
    assert item["met"] == 123
    assert item["data_product_1"] == str(1234.56)
