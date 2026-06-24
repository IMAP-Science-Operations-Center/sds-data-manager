"""Tests for the Download API."""

import datetime
from unittest import mock

from sds_data_manager.lambda_code.SDSCode.api_lambdas import download_api
from sds_data_manager.lambda_code.SDSCode.database import models


@mock.patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.download_api.is_authenticated_user"
)
@mock.patch("sds_data_manager.lambda_code.SDSCode.api_lambdas.download_api.is_released")
def test_object_exists(mock_is_released, mock_is_authenticated, s3_client):
    """Test that we get a presigned url back for an object that exists."""
    # Setup mocks
    mock_is_authenticated.return_value = True
    mock_is_released.return_value = True

    science_file = "imap/swe/l1a/2010/01/imap_swe_l1a_test_20100101_v000.cdf"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=science_file,
        Body=b"test",
    )
    event = {
        "version": "2.0",
        "routeKey": "GET /api-key/download",
        "rawPath": "/api-key/download",
        "pathParameters": {"proxy": science_file},
    }
    response = download_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 302
    assert "Location" in response["headers"]
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in response["headers"]["Location"]
    assert "download_url" in response["body"]


@mock.patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.download_api.is_authenticated_user"
)
def test_nonexistant_object(mock_is_authenticated):
    """Test that objects exist in s3 fails."""
    # Setup mock
    mock_is_authenticated.return_value = True

    event = {
        "version": "2.0",
        "routeKey": "GET /download",
        "rawPath": "/download",
        "pathParameters": {"proxy": "bad_path/bad_file.txt"},
    }

    response = download_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 404


@mock.patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.download_api.is_authenticated_user"
)
def test_input_parameters_missing(mock_is_authenticated):
    """Test that required input parameters exist."""
    # Setup mock
    mock_is_authenticated.return_value = True

    empty_para_event = {
        "version": "2.0",
        "routeKey": "GET /download",
        "rawPath": "/download",
        # No pathParameters
    }

    response = download_api.lambda_handler(event=empty_para_event, context=None)
    assert response["statusCode"] == 400


def test_auth_path_unreleased_file_access(session, s3_client):
    """Test that authenticated paths can access unreleased files."""
    # Create an unreleased file in the database
    filepath = "test/file/path/imap_hit_l0_raw_20210101_v001.0001.pkts"
    s3_path = filepath  # For simplicity, use the same path for S3 and database

    # Add the file to S3
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=s3_path,
        Body=b"test content",
    )

    # Create file entry in the database with released=False
    metadata_params = {
        "file_path": filepath,
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20210101", "%Y%m%d"),
        "major_version": 1,
        "minor_version": 1,
        "extension": "pkts",
        "ingestion_date": datetime.datetime.strptime(
            "2021-01-01 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
        "released": False,  # Mark as unreleased
    }

    # Add data to the ScienceFiles table
    session.add(models.ScienceFiles(**metadata_params))
    session.commit()

    # Test with public path (no api-key or auth in path)
    event_public = {
        "version": "2.0",
        "routeKey": "GET /download",
        "rawPath": "/download",
        "pathParameters": {"proxy": filepath},
    }
    response_public = download_api.lambda_handler(event=event_public, context=None)

    # Public path should be denied access to unreleased file
    assert response_public["statusCode"] == 403
    assert "part of a public release yet" in response_public["body"]

    # Test with authenticated path (api-key in path)
    event_auth = {
        "version": "2.0",
        "routeKey": "GET /api-key/download",
        "rawPath": "/api-key/download",
        "pathParameters": {"proxy": filepath},
    }
    response_auth = download_api.lambda_handler(event=event_auth, context=None)

    # Authenticated path should get access to unreleased file
    assert response_auth["statusCode"] == 302
    assert "Location" in response_auth["headers"]
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in response_auth["headers"]["Location"]
    assert "download_url" in response_auth["body"]


def test_released_file_access(session, s3_client):
    """Test that released files can be accessed without authentication."""
    # Use the requested file path
    filepath = "imap/spice/spin/imap_2025_274_2025_274_01.spin"
    s3_path = filepath  # For simplicity, use the same path for S3 and database

    # Add the file to S3
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=s3_path,
        Body=b"test content",
    )

    # Create file entry in the spin_files table with released=True
    metadata_params = {
        "file_path": filepath,
        "start_date": datetime.datetime.strptime("2025-10-01", "%Y-%m-%d"),
        "end_date": datetime.datetime.strptime("2025-10-01", "%Y-%m-%d"),
        "version": "01",
        "ingestion_date": datetime.datetime.strptime(
            "2025-10-01 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
        "released": True,  # Mark as released
    }

    # Add data to the SpinFiles table
    session.add(models.SpinFiles(**metadata_params))
    session.commit()

    # Test with public path (no api-key or auth in path)
    event_public = {
        "version": "2.0",
        "routeKey": "GET /download",
        "rawPath": "/download",
        "pathParameters": {"proxy": filepath},
    }
    response_public = download_api.lambda_handler(event=event_public, context=None)

    # Public path should get access to released file
    assert response_public["statusCode"] == 302
    assert "Location" in response_public["headers"]
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in response_public["headers"]["Location"]
    assert "download_url" in response_public["body"]
