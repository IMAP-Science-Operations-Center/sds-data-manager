"""Tests for the I-ALiRT Packet Query API."""

import json

from sds_data_manager.lambda_code.IAlirtCode import ialirt_packets_query_api


def test_packet_query_start_only(s3_client, monkeypatch):
    """Test that time_utc_start alone returns all files from that time onward."""
    s3_client.create_bucket(Bucket="test-data-bucket")
    monkeypatch.setenv("S3_BUCKET", "test-data-bucket")
    monkeypatch.setenv("REGION", "us-west-2")

    # DOY 148 = May 28
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_27",
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_28",
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_10_00_00",  # before start, excluded
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_149_16_24_27",  # different DOY, excluded
        Body=b"test-data",
    )

    event = {"queryStringParameters": {"time_utc_start": "2025-05-28T16:24:27"}}

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert sorted(response_data["files"]) == [
        "iois_1_packets_2025_148_16_24_27",
        "iois_1_packets_2025_148_16_24_28",
    ]


def test_packet_query_start_and_end(s3_client, monkeypatch):
    """Test that time_utc_start + time_utc_end returns only files within range."""
    s3_client.create_bucket(Bucket="test-data-bucket")
    monkeypatch.setenv("S3_BUCKET", "test-data-bucket")
    monkeypatch.setenv("REGION", "us-west-2")

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_00_00",  # before start
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_27",  # within range
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_17_00_00",  # after end
        Body=b"test-data",
    )

    event = {
        "queryStringParameters": {
            "time_utc_start": "2025-05-28T16:24:00",
            "time_utc_end": "2025-05-28T16:30:00",
        }
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert response_data["files"] == ["iois_1_packets_2025_148_16_24_27"]


def test_packet_query_no_params():
    """Test that providing no parameters returns a 400."""
    event = {"queryStringParameters": {}}

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert "year" in response["body"]


def test_packet_query_invalid_start_format():
    """Test that an invalid time_utc_start format returns a 400."""
    event = {"queryStringParameters": {"time_utc_start": "28/05/2025 16:24"}}

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert "isoformat" in response["body"]


def test_packet_query_end_before_start():
    """Test that time_utc_end <= time_utc_start returns a 400."""
    event = {
        "queryStringParameters": {
            "time_utc_start": "2025-05-28T17:00:00",
            "time_utc_end": "2025-05-28T16:00:00",
        }
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert "time_utc_end" in response["body"]


def test_packet_query_individual_params(s3_client, monkeypatch):
    """Test that individual year/doy/hh/mm params build the correct S3 prefix."""
    s3_client.create_bucket(Bucket="test-data-bucket")
    monkeypatch.setenv("S3_BUCKET", "test-data-bucket")
    monkeypatch.setenv("REGION", "us-west-2")

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_27",
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_148_16_24_28",
        Body=b"test-data",
    )
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="packets/iois_1_packets_2025_149_16_24_27",  # different DOY
        Body=b"test-data",
    )

    event = {
        "queryStringParameters": {
            "year": "2025",
            "doy": "148",
            "hh": "16",
            "mm": "24",
        }
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert sorted(response_data["files"]) == [
        "iois_1_packets_2025_148_16_24_27",
        "iois_1_packets_2025_148_16_24_28",
    ]


def test_packet_query_individual_params_invalid_date():
    """Test that invalid year/doy returns a 400 in individual params mode."""
    event = {
        "queryStringParameters": {
            "year": "abcd",
            "doy": "999",
        }
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert "Invalid year or day format" in response["body"]


def test_packet_query_mixed_modes_returns_400():
    """Test that mixing UTC range and individual params returns a 400."""
    event = {
        "queryStringParameters": {
            "time_utc_start": "2025-05-28T16:24:00",
            "year": "2025",
        }
    }

    response = ialirt_packets_query_api.lambda_handler(event=event, context=None)
    assert response["statusCode"] == 400
    assert "Cannot mix" in response["body"]
