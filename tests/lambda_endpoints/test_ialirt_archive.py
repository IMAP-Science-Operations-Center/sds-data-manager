"""Test the I-Alirt archive lambda function."""

from datetime import datetime, timedelta, timezone

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_archive import lambda_handler


@pytest.fixture
def populate_algorithm_table(setup_dynamodb):
    """Populate the algorithm table with test entries."""
    algorithm_table = setup_dynamodb["algorithm_table"]
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    items = [
        {
            "apid": 478,
            "met": 111,
            "met_in_utc": (yesterday + timedelta(seconds=1)).isoformat(),
            "last_modified": (yesterday + timedelta(seconds=1)).isoformat(),
            "data_product_1": "3.14",
        },
        {
            "apid": 478,
            "met": 222,
            "met_in_utc": (yesterday - timedelta(seconds=1)).isoformat(),
            "last_modified": (yesterday - timedelta(seconds=1)).isoformat(),
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
