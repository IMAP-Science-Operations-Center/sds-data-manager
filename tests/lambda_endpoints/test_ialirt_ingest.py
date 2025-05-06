"""Test the I-Alirt ingest lambda function."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import (
    lambda_handler,
    parse_packet,
    query_filenames,
)


@pytest.fixture
def populate_table(setup_dynamodb):
    """Populate DynamoDB table."""
    ingest_table = setup_dynamodb["ingest_table"]

    items = [
        {
            "apid": 478,
            "met": 123,
            "ingest_time": "2021-01-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
        {
            "apid": 478,
            "met": 124,
            "ingest_time": "2021-02-01T00:00:00Z",
            "packet_blob": b"binary_data_string",
        },
    ]
    for item in items:
        ingest_table.put_item(Item=item)

    return items


@pytest.fixture
def s3_test_packet(s3_client):
    """Add a fake binary packet file to the mock S3 bucket."""
    test_file = "iois_1_packets_YYYY_DOY_HH_MM_SS.ccsds"

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=test_file,
        Body=b"dummy test data",
    )

    return test_file


def test_lambda_handler(setup_dynamodb):
    """Test the lambda_handler function."""
    # Mock event data
    algorithm_table = setup_dynamodb["algorithm_table"]

    event = {
        "region": "us-west-2",
        "detail": {
            "object": {"key": "packets/file.txt"},
            "bucket": {"name": "test-data-bucket"},
        },
    }

    lambda_handler(event, {})

    response = algorithm_table.get_item(
        Key={
            "apid": 478,
            "met": 123,
        }
    )
    item = response.get("Item")

    assert item["met"] == 123
    assert item["insert_time"] == "2021-01-01T00:00:00Z"
    assert item["product_name"] == "hit_product_1"
    assert item["data_product_1"] == str(1234.56)


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.packet_file_to_datasets")
def test_parse_packet_s3(mock_packet_file_to_datasets, s3_test_packet, tmp_path):
    """Test parse_packet function."""
    expected_result = {"123": "parsed dataset"}
    mock_packet_file_to_datasets.return_value = expected_result

    filename = Path(s3_test_packet).name

    result = parse_packet(
        filename, "test-data-bucket", s3_test_packet, download_dir=str(tmp_path)
    )

    assert result == expected_result
    mock_packet_file_to_datasets.assert_called_once()

    # Check if file was downloaded
    real_tmp_file = tmp_path / filename
    assert real_tmp_file.exists()


def test_query_filenames(s3_client):
    """Test the query_filenames function."""
    bucket = "test-data-bucket"
    region = "us-west-2"
    now = datetime(2025, 4, 28, 16, 5, 0, tzinfo=timezone.utc)

    # Files in the desired time range
    inside_range_keys = [
        "packets/iois_1_packets_2025_118_16_01_00",
        "packets/iois_1_packets_2025_118_16_03_00",
        "packets/iois_1_packets_2025_118_16_04_00",
    ]

    outside_range_key = "packets/iois_1_packets_2025_118_15_59_00"

    for key in [*inside_range_keys, outside_range_key]:
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"dummy data")

    result = query_filenames(bucket, region, now)

    assert sorted(result) == sorted(inside_range_keys)


def test_query_filenames_crossing_hour_boundary(s3_client):
    """Test query_filenames when crossing hour boundary."""
    bucket = "test-data-bucket"
    region = "us-west-2"

    now = datetime(2025, 4, 28, 1, 2, 0, tzinfo=timezone.utc)

    first_prefix_key = "packets/iois_1_packets_2025_118_00_58_00"
    second_prefix_key = "packets/iois_1_packets_2025_118_01_00_00"
    outside_range_key = "packets/iois_1_packets_2025_118_00_50_00"

    for key in [first_prefix_key, second_prefix_key, outside_range_key]:
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"dummy data")

    result = query_filenames(bucket, region, now)

    assert sorted(result) == sorted([first_prefix_key, second_prefix_key])
