"""Test the I-Alirt archive lambda function."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
            "ttj2000ns": 1234567890123456789,
            "met_in_utc": (yesterday + timedelta(seconds=1)).isoformat(),
            "data_product_1": "3.14",
        },
        {
            "apid": 478,
            "met": 222,
            "ttj2000ns": 1234567890123450000,
            "met_in_utc": (yesterday - timedelta(seconds=1)).isoformat(),
            "data_product_2": "2.71",
        },
    ]
    for item in items:
        algorithm_table.put_item(Item=item)

    return items


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_archive.write_cdf")
def test_archive_lambda_handler(mock_write_cdf, populate_algorithm_table, tmp_path):
    """Test archive_lambda_handler function."""
    mock_path = tmp_path / "mock_output.cdf"
    mock_path.touch()
    mock_write_cdf.return_value = mock_path

    lambda_handler({}, {})
