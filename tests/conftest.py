"""Setup items for all test types."""

import os

import boto3
import pytest
from moto import mock_dynamodb


@pytest.fixture
def setup_data_table():
    """Initialize DynamoDB resource and create table."""
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    os.environ["INGEST_TABLE"] = "imap-ingest-table"
    os.environ["DATA_TABLE"] = "imap-data-table"

    with mock_dynamodb():
        # Initialize DynamoDB resource
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        data_table = dynamodb.create_table(
            TableName=os.environ["DATA_TABLE"],
            KeySchema=[
                # Partition key
                {"AttributeName": "instrument", "KeyType": "HASH"},
                # Sort key
                {"AttributeName": "time_utc", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "instrument", "AttributeType": "S"},
                {"AttributeName": "time_utc", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "data_table": data_table,
        }
