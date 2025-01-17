"""Setup items for all test types."""

import os

import boto3
import pytest
from moto import mock_dynamodb


@pytest.fixture()
def setup_dynamodb():
    """Initialize DynamoDB resource and create table."""
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    os.environ["INGEST_TABLE"] = "imap-ingest-table"
    os.environ["ALGORITHM_TABLE"] = "imap-algorithm-table"

    with mock_dynamodb():
        # Initialize DynamoDB resource
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        ingest_table = dynamodb.create_table(
            TableName=os.environ["INGEST_TABLE"],
            KeySchema=[
                # Partition key
                {"AttributeName": "apid", "KeyType": "HASH"},
                # Sort key
                {"AttributeName": "met", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "apid", "AttributeType": "N"},
                {"AttributeName": "met", "AttributeType": "N"},
                {"AttributeName": "ingest_time", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "ingest_time",
                    "KeySchema": [
                        {"AttributeName": "apid", "KeyType": "HASH"},
                        {"AttributeName": "ingest_time", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        algorithm_table = dynamodb.create_table(
            TableName=os.environ["ALGORITHM_TABLE"],
            KeySchema=[
                # Partition key
                {"AttributeName": "apid", "KeyType": "HASH"},
                # Sort key
                {"AttributeName": "met", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "apid", "AttributeType": "N"},
                {"AttributeName": "met", "AttributeType": "N"},
                {"AttributeName": "insert_time", "AttributeType": "S"},
                {"AttributeName": "product_name", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "insert_time",  # Unique index name
                    "KeySchema": [
                        {"AttributeName": "apid", "KeyType": "HASH"},
                        {"AttributeName": "insert_time", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "product_name",  # Unique index name
                    "KeySchema": [
                        {"AttributeName": "apid", "KeyType": "HASH"},
                        {"AttributeName": "product_name", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "ingest_table": ingest_table,
            "algorithm_table": algorithm_table,
        }
