"""Test the I-Alirt ingest lambda function."""

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import lambda_handler


@pytest.fixture()
def populate_table(setup_dynamodb):
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


def test_lambda_handler(setup_dynamodb):
    """Test the lambda_handler function."""
    # Mock event data
    ingest_table = setup_dynamodb["ingest_table"]
    algorithm_table = setup_dynamodb["algorithm_table"]

    event = {"detail": {"object": {"key": "packets/file.txt"}}}

    lambda_handler(event, {})

    response = ingest_table.get_item(
        Key={
            "apid": 478,
            "met": 123,
        }
    )
    item = response.get("Item")

    assert item is not None
    assert item["met"] == 123
    assert item["packet_blob"] == b"binary_data_string"

    response = algorithm_table.get_item(
        Key={
            "apid": 478,
            "met": 123,
        }
    )
    item = response.get("Item")

    assert item is not None
    assert item["met"] == 123
    assert item["insert_time"] == "2021-01-01T00:00:00Z"
    assert item["product_name"] == "hit_product_1"
    assert item["data_product_1"] == str(1234.56)
