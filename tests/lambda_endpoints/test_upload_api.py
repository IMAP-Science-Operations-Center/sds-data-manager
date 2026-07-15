"""Tests for the Upload API."""

import json
import os

from sds_data_manager.lambda_code.SDSCode.api_lambdas import upload_api


def test_spice_file_upload(s3_client, spice_file):
    """Test spice files being uploaded."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": spice_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 200

    # Try to upload over a pre-existing file and we should get a 409
    # Note that we are using pre-signed urls so we haven't actually
    # uploaded anything in the previous call, only gotten back a url
    # So we need to upload a file to s3 to simulate a pre-existing file
    s3_client.put_object(
        Bucket=os.getenv("S3_BUCKET"),
        Key=spice_file,
        Body=b"test",
    )
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": spice_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 409


def test_science_file_upload(s3_client, science_file):
    """Test science files being uploaded."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": science_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 200

    # Try to upload again and we should get a 409 duplicate error
    s3_client.put_object(
        Bucket=os.getenv("S3_BUCKET"),
        Key=science_file,
        Body=b"test",
    )
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": science_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 409


def test_ancillary_file_upload(s3_client, ancillary_file):
    """Test ancillary files being uploaded."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": ancillary_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 200

    # Try to upload again and we should get a 409 duplicate error
    s3_client.put_object(
        Bucket=os.getenv("S3_BUCKET"),
        Key=ancillary_file,
        Body=b"test",
    )
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": ancillary_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 409


def test_cadence_file_upload(s3_client, dependency_file):
    """Test cadence files being uploaded."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": dependency_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 200

    # Try to upload again and we should get a 409 duplicate error
    s3_client.put_object(
        Bucket=os.getenv("S3_BUCKET"),
        Key=dependency_file,
        Body=b"test",
    )
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": dependency_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 409


def test_input_parameters_missing():
    """Test that required input parameters exist."""
    empty_para_event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
        # No pathParameters
    }

    response = upload_api.lambda_handler(event=empty_para_event, context=None)
    assert response["statusCode"] == 400


def test_incorrect_file_type(s3_client, invalid_file):
    """Test that an error if thrown when file does not match any type."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": invalid_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "write", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400


def test_upload_denied_for_read_scope(s3_client, science_file):
    """Test that upload is denied for API keys with read scope."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": science_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "read", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 403
    assert response["body"] == json.dumps(
        "Upload access denied. Your API key has read permissions."
    )

    # case when no scope is provided in the authorizer context
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": science_file},
        "requestContext": {
            "authorizer": {"lambda": {"scope": "", "apiKey": "test-key"}}
        },
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 403
    assert response["body"] == json.dumps(
        "Upload access denied. Please provide a valid API key with upload permissions."
    )
