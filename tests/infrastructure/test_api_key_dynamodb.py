#!/usr/bin/env python3
"""Test script to verify API key functionality with DynamoDB.

This script tests both the management operations and authorization logic
without requiring deployment to AWS Lambda.

Usage:
    python test_api_key_dynamodb.py
"""

import os

import boto3
import pytest
from moto import mock_dynamodb

TABLE_NAME = "imap-sdc-api-keys"


@pytest.fixture
def dynamodb_table():
    """Create a mock DynamoDB table for testing."""
    # Set AWS region for boto3
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    with mock_dynamodb():
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "api_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "api_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


def test_api_key_management(dynamodb_table):
    """Test API key management operations."""
    # Import after mocks are set up
    from sds_data_manager.lambda_code.authorization.lambda_api_key_authorizer import (
        lambda_handler,
    )
    from scripts.authorization.manage_api_keys import (
        add_key_to_db,
        get_keys,
        remove_key_from_db,
        update_permission,
    )

    # Test adding a key
    test_key = "test123456789abcdef"
    add_key_to_db(
        test_key, "Test User", "test@example.com", "full", "2024-01-01T00:00:00"
    )

    # Test retrieving keys
    keys = get_keys()
    assert test_key in keys
    assert keys[test_key]["owner"] == "Test User"
    assert keys[test_key]["email"] == "test@example.com"
    assert keys[test_key]["scope"] == "full"

    # Test getting metadata for authorization (direct table access)
    metadata = dynamodb_table.get_item(Key={"api_key": test_key}).get("Item")
    assert metadata is not None
    assert metadata["owner"] == "Test User"

    # Test authorization logic
    event = {"headers": {"x-api-key": test_key}, "rawPath": "/test-endpoint"}
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is True
    assert result["context"]["apiKey"] == test_key

    # Test authorization with invalid key
    event["headers"]["x-api-key"] = "invalid-key"
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is False

    # Test scope-based authorization
    event["headers"]["x-api-key"] = test_key
    event["rawPath"] = "/ialirt-db-query/test"
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is True  # "full" scope should allow access

    # Test that permissions were updated.
    update_permission("Test User", "test@example.com", "read")
    metadata = dynamodb_table.get_item(Key={"api_key": test_key}).get("Item")
    assert metadata["scope"] == "read"

    # Test removing a key
    remove_key_from_db(test_key)
    keys = get_keys()
    assert test_key not in keys


def test_scope_restrictions(dynamodb_table):
    """Test scope-based access restrictions."""
    # Import after mocks are set up
    from sds_data_manager.lambda_code.authorization.lambda_api_key_authorizer import (
        lambda_handler,
    )
    from scripts.authorization.manage_api_keys import (
        add_key_to_db,
    )

    # Add a key with read-only scope
    read_only_key = "readonly123456789ab"
    add_key_to_db(
        read_only_key,
        "Read Only User",
        "readonly@example.com",
        "read",
        "2024-01-01T00:00:00",
    )

    # Test access to spaceweather-priority with read scope (should be allowed)
    event = {
        "headers": {"x-api-key": read_only_key},
        "rawPath": "/space-weather-priority/test",
        "requestContext": {"http": {"method": "GET"}},
    }
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is True  # Read scope can access queries

    # Test access to regular endpoint with read scope
    event["rawPath"] = "/regular-endpoint"
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is True  # Should be allowed

    # Test write restriction for read-only scope
    event["requestContext"] = {"http": {"method": "PUT"}}
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is False  # Write operations should be denied

    # Test upload restriction for read-only scope
    event["rawPath"] = "/api-key/upload/test"
    event["requestContext"] = {"http": {"method": "POST"}}
    result = lambda_handler(event, {})
    assert result["isAuthorized"] is False  # Upload should be denied
