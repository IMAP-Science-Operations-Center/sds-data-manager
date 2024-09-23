"""Test the IAlirt ingest lambda function."""

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import lambda_handler


@pytest.fixture()
def populate_table(table):
    """Populate DynamoDB table."""
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
        table.put_item(Item=item)

    return items


def test_lambda_handler(table):
    """Test the lambda_handler function."""
    # Mock event data
    event = {"detail": {"object": {"key": "packets/file.txt"}}}

    lambda_handler(event, {})

    response = table.get_item(
        Key={
            "apid": 478,
            "met": 123,
        }
    )
    item = response.get("Item")

    assert item is not None
    assert item["met"] == 123
    assert item["packet_blob"] == b"binary_data_string"
