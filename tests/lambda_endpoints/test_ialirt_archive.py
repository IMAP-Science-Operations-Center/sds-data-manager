"""Test the I-Alirt archive lambda function."""

import pytest
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key

from sds_data_manager.lambda_code.IAlirtCode.ialirt_archive import lambda_handler


@pytest.fixture
def populate_algorithm_table(setup_dynamodb):
    """Populate the algorithm table with test entries."""
    algorithm_table = setup_dynamodb["algorithm_table"]

    items = [
        {
            "apid": 478,
            "met": 111,
            "insert_time": (datetime.utcnow() - timedelta(hours=12)).isoformat(),
            "product_name": "test_product",
            "data_product_1": "3.14",
        },
        {
            "apid": 478,
            "met": 222,
            "insert_time": (datetime.utcnow() - timedelta(days=2)).isoformat(),
            "product_name": "test_product",
            "data_product_2": "2.71",
        },
    ]
    for item in items:
        algorithm_table.put_item(Item=item)

    return items


def test_archive_lambda_handler(setup_dynamodb, populate_algorithm_table, monkeypatch):
    """Test archive_lambda_handler function."""
    algorithm_table = setup_dynamodb["algorithm_table"]

    monkeypatch.setenv("ALGORITHM_TABLE", algorithm_table.table_name)

    lambda_handler({}, {})
    response = algorithm_table.query(
        IndexName="insert_time",
        KeyConditionExpression="apid = :apid_val AND insert_time BETWEEN :start AND :end",
        ExpressionAttributeValues={
            ":apid_val": 478,
            ":start": (datetime.utcnow() - timedelta(days=1)).isoformat(),
            ":end": datetime.utcnow().isoformat(),
        },
    )

    items = response["Items"]
    assert len(items) == 1
    assert items[0]["met"] == 111
    assert items[0]["data_product_1"] == "3.14"

