"""Tests for the I-ALiRT DB Query API Lambda function."""

import os
import json
import boto3
import pytest
from moto import mock_dynamodb

from sds_data_manager.lambda_code.IAlirtCode import ialirt_db_query_api


@pytest.fixture
def dynamodb_table():
    """Create a mocked DynamoDB table and populate it with sample data."""
    os.environ["TABLE_NAME"] = "imap-algorithm-table"
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    with mock_dynamodb():
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="imap-algorithm-table",
            KeySchema=[
                {"AttributeName": "apid", "KeyType": "HASH"},
                {"AttributeName": "met", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "apid", "AttributeType": "N"},
                {"AttributeName": "met", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()

        # Insert sample items into the table.
        table.put_item(Item={"apid": 478, "met": 101, "product_name": "hit_product_1", "data": "item1"})
        table.put_item(Item={"apid": 478, "met": 120, "product_name": "hit_product_1", "data": "item2"})
        table.put_item(Item={"apid": 478, "met": 130, "product_name": "other_product", "data": "item3"})
        table.put_item(Item={"apid": 123, "met": 110, "product_name": "hit_product_1", "data": "item4"})

        yield table


def test_query_with_product_name(dynamodb_table):
    """Test query API returns items matching a product_name filter."""
    event = {
        "queryStringParameters": {
            "apid": "478",
            "met_start": "100",
            "met_end": "125",
            "product_name": "hit_product_1",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    assert response["statusCode"] == 200

    items = json.loads(response["body"])
    # Expecting two items:
    #   { "apid": 478, "met": 101, "product_name": "hit_product_1", "data": "item1" }
    #   { "apid": 478, "met": 120, "product_name": "hit_product_1", "data": "item2" }
    assert len(items) == 2
    returned_data = sorted(item["data"] for item in items)
    assert returned_data == ["item1", "item2"]


def test_query_without_product_name(dynamodb_table):
    """Test query API returns items when no product_name filter is provided."""
    event = {
        "queryStringParameters": {
            "apid": "478",
            "met_start": "100",
            "met_end": "125",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    assert response["statusCode"] == 200

    items = json.loads(response["body"])
    # Without the product_name filter, items with met values within 100-125 are:
    #   { "apid": 478, "met": 101, ... } and { "apid": 478, "met": 120, ... }
    assert len(items) == 2
    returned_data = sorted(item["data"] for item in items)
    assert returned_data == ["item1", "item2"]


def test_query_no_results(dynamodb_table):
    """Test query API returns an empty list when no items match."""
    event = {
        "queryStringParameters": {
            "apid": "478",
            "met_start": "200",
            "met_end": "300",
        }
    }
    response = ialirt_db_query_api.lambda_handler(event, context=None)
    assert response["statusCode"] == 200

    items = json.loads(response["body"])
    assert len(items) == 0
