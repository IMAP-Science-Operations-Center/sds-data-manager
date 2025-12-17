"""Test the I-Alirt archive lambda function."""

from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_archive import lambda_handler


@pytest.fixture
def populate_data_table(setup_data_table):
    """Populate the algorithm table with test entries."""
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


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_archive.write_cdf")
def test_archive_lambda_handler(mock_write_cdf, populate_data_table, tmp_path):
    """Test archive_lambda_handler function."""
    mock_path = tmp_path / "mock_output.cdf"
    mock_path.touch()
    mock_write_cdf.return_value = mock_path

    lambda_handler({}, {})
