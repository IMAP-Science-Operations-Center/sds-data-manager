"""Test the I-Alirt archive lambda function."""

from datetime import datetime, timedelta

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_archive import lambda_handler


@pytest.fixture
def populate_algorithm_table(setup_dynamodb):
    """Populate the algorithm table with test entries."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    items = [
        {
            "apid": 478,
            "met": 111,
            "insert_time": (yesterday + timedelta(seconds=1)).isoformat(),
            "product_name": "test_product",
            "data_product_1": "3.14",
        },
        {
            "apid": 478,
            "met": 222,
            "insert_time": (yesterday - timedelta(seconds=1)).isoformat(),
            "product_name": "test_product",
            "data_product_2": "2.71",
        },
    ]
    for item in items:
        algorithm_table.put_item(Item=item)

    return items


def test_archive_lambda_handler(populate_algorithm_table):
    """Test archive_lambda_handler function."""
    response = lambda_handler({}, {})

    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == 111
    assert items[0]["data_product_1"] == "3.14"
