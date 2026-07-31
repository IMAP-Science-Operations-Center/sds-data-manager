"""Setup items for all test types."""

import os
from contextlib import contextmanager
from unittest.mock import patch

import boto3
import pytest
from moto import mock_dynamodb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database.models import Base


@contextmanager
def in_memory_session(engine=None):
    """Yield an in-memory SQLite session with ``db.Session`` patched to it.

    Shared scaffolding for the DB-backed ``session`` fixtures. Pass a pre-built
    ``engine`` (e.g. with event listeners already attached) or let it build a
    default in-memory engine.
    """
    if engine is None:
        engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with patch.object(db, "Session") as mock_session:
        session = sessionmaker(bind=engine)()
        mock_session.return_value = session
        try:
            yield session
        finally:
            session.rollback()
            session.close()
            Base.metadata.drop_all(engine)


@pytest.fixture
def setup_dynamodb():
    """Initialize DynamoDB resource and create table."""
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    os.environ["INGEST_TABLE"] = "imap-ingest-table"
    os.environ["ALGORITHM_TABLE"] = "ialirt-algorithm-table"

    with mock_dynamodb():
        # Initialize DynamoDB resource
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

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
                {"AttributeName": "met_in_utc", "AttributeType": "S"},
                {"AttributeName": "last_modified", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "met_in_utc",  # Unique index name
                    "KeySchema": [
                        {"AttributeName": "apid", "KeyType": "HASH"},
                        {"AttributeName": "met_in_utc", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "last_modified",  # Unique index name
                    "KeySchema": [
                        {"AttributeName": "apid", "KeyType": "HASH"},
                        {"AttributeName": "last_modified", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "algorithm_table": algorithm_table,
        }


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
