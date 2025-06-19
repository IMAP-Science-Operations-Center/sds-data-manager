"""Tests for the I-ALiRT Packet Query API."""

import json

from sds_data_manager.lambda_code.IAlirtCode import ialirt_packets_query_api


def test_packet_query_within_range(s3_client, monkeypatch):
    """Test that the packet query API returns matching files by partial datetime."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    # Patch environment variables
    monkeypatch.setenv("S3_BUCKET", "test-data-bucket")
    monkeypatch.setenv("REGION", "us-west-2")

    # Add files matching and not matching the prefix
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_27.bin",
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_28.bin",
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_149_16_24_27.bin",  # Different DOY
        Body=b"test-data",
    )

    event = {
        "queryStringParameters": {"year": "2025", "doy": "148", "hh": "16", "mm": "24"}
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert sorted(response_data["files"]) == [
        "iois_1_packets_2025_148_16_24_27.bin",
        "iois_1_packets_2025_148_16_24_28.bin",
    ]


def test_packet_query_invalid_date(s3_client):
    """Test that an error is returned for invalid year/doy input."""
    event = {
        "queryStringParameters": {
            "year": "abcd",
            "doy": "999",  # also invalid
        }
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert "Invalid year or day format" in response["body"]
