"""Tests for the I-ALiRT Packet Query API."""

import json

from sds_data_manager.lambda_code.IAlirtCode import ialirt_packets_query_api


def test_query_packet_prefix(s3_client):
    """Test that the query API returns files matching packet prefix."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    # Add files with similar prefixes
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_27.bin",
        Body=b"test",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_28.bin",
        Body=b"test",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_149_16_24_27.bin",  # not a match
        Body=b"test",
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


def test_packet_invalid_date_format():
    """Test that an error is returned for invalid packet date formats."""
    event = {"queryStringParameters": {"year": "invalid", "doy": "also_invalid"}}

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "Invalid year or day format. Use YYYY and DOY." in response["body"]
