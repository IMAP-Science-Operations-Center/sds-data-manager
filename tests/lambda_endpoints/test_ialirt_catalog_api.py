"""Tests for the I-ALiRT Catalog API."""

import json

from sds_data_manager.lambda_code.IAlirtCode import ialirt_catalog_api


def test_bad_dates_ialirt_catalog_api(s3_client):
    """Test a failing call to the catalog endpoint due to bad dates."""
    event = {
        "start_date": "2025-02-06",
        "end_date": "2025-02-06",
        "station": "station1",
    }
    response = ialirt_catalog_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert response["body"] == (
        "The start date of the requested time frame is not "
        "before the end date. Please supply a valid time "
        "range."
    )


def test_too_large_time_range_ialirt_catalog_api(s3_client):
    """Test a failing call to the endpoint caused by dates that are too far apart."""
    event = {
        "start_date": "2025-02-06",
        "end_date": "2025-03-20",
        "station": "station1",
    }
    response = ialirt_catalog_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert response["body"] == (
        "The end date of the requested time frame must not be "
        "more than 30 days after the start date. Please supply "
        "a valid time range."
    )


def test_no_files_ialirt_catalog_api(s3_client):
    """Test unsuccessful call to the endpoint caused by an empty response from s3."""
    event = {
        "start_date": "2025-02-06",
        "end_date": "2025-02-07",
        "station": "station1",
    }
    response = ialirt_catalog_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 404


def test_success_ialirt_catalog_api(s3_client):
    """Test a successful call to the catalog endpoint."""
    test_file1 = "pointing_schedules/station1/20250206/20250206_station1_01.txt"
    test_file2 = "pointing_schedules/station1/20250206/20250206_station1_02.txt"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=test_file1,
        Body=b"Hello world 1",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=test_file2,
        Body=b"Hello world 2",
    )
    event = {
        "start_date": "2025-02-06",
        "end_date": "2025-02-07",
        "station": "station1",
    }
    response = ialirt_catalog_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["2025-02-06 00:00:00"]["file_names"] == [
        "20250206_station1_01.txt",
        "20250206_station1_02.txt",
    ]
