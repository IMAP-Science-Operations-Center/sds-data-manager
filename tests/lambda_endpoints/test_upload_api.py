"""Tests for the Upload API."""

import os
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.SDSCode import upload_api


@pytest.fixture(autouse=True)
def setup_s3(s3_client):
    """Populate the mocked s3 client with a bucket and a file.

    Each test below will use this fixture by default.
    """
    bucket_name = os.getenv("S3_BUCKET")
    s3_client.create_bucket(
        Bucket=bucket_name,
    )
    result = s3_client.list_buckets()
    assert len(result["Buckets"]) == 1
    assert result["Buckets"][0]["Name"] == bucket_name

    # patch the mocked client into the upload_api module
    # These have to be patched in because they were imported
    # prior to test discovery and would have the default values (None)
    with (
        patch.object(upload_api, "S3_CLIENT", s3_client),
        patch.object(upload_api, "BUCKET_NAME", bucket_name),
    ):
        yield s3_client


def test_spice_file_upload(s3_client, spice_file):
    """Test spice files being uploaded."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "pathParameters": {"proxy": spice_file},
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
    }
    response = upload_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 409


def test_input_parameters_missing():
    """Test that required input parameters exist."""
    empty_para_event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        # No pathParameters
    }

    response = upload_api.lambda_handler(event=empty_para_event, context=None)
    assert response["statusCode"] == 400
