"""Tests for the I-ALiRT Query API."""

import json

from sds_data_manager.lambda_code.IAlirtCode import ialirt_query_api


def test_query_within_date_range(s3_client):
    """Test that the query API returns files within the specified date range."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    # Adding files within and outside of the desired date range
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="logs/2023/141/IOIS_msgs_flight_2023_141_16_54_46.txt",
        Body=b"test",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="logs/2024/141/IOIS_msgs_flight_2024_141_16_54_46.txt",
        Body=b"test",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="logs/2025/141/IOIS_msgs_flight_2025_141_16_54_46.txt",
        Body=b"test",
    )

    event = {
        "queryStringParameters": {"start": "2024141165445", "end": "2024141165447"}
    }

    response = ialirt_query_api.lambda_handler(event=event, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert len(response_data["files"]) == 1


def test_invalid_date_format():
    """Test that an error is returned for invalid date formats."""
    event = {"queryStringParameters": {"start": "invalid_date", "end": "invalid_date"}}

    response = ialirt_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "Invalid date format" in response["body"]
