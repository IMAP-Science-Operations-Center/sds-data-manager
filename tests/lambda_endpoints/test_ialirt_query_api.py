"""Tests for the I-ALiRT Query API."""

import json

from sds_data_manager.lambda_code.IAlirtCode import ialirt_query_api


def test_query_within_date_range(s3_client):
    """Test that the query API returns files within the specified date range."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    # Adding files within and outside of the desired date range
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="logs/flight_iois_1.log.2024-141T16-55-46_123456.txt",
        Body=b"test",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="logs/flight_iois_1.log.2024-141T16-54-46_123456.txt",
        Body=b"test",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="logs/flight_iois_1.log.2025-141T16-54-46_123456.txt",
        Body=b"test",
    )

    event = {"queryStringParameters": {"year": "2024", "doy": "141", "instance": "1"}}

    response = ialirt_query_api.lambda_handler(event=event, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert response_data["files"] == [
        "flight_iois_1.log.2024-141T16-54-46_123456.txt",
        "flight_iois_1.log.2024-141T16-55-46_123456.txt",
    ]


def test_invalid_date_format():
    """Test that an error is returned for invalid date formats."""
    event = {"queryStringParameters": {"year": "invalid_date", "doy": "invalid_date"}}

    response = ialirt_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "Invalid year or day format. Use YYYY and DOY." in response["body"]
