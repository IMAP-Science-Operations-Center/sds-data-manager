"""Test the I-Alirt ingest lambda function."""

from datetime import datetime, timezone

from sds_data_manager.lambda_code.IAlirtCode.ialirt_realtime import (
    lambda_handler,
    query_filenames,
    read_ingest_logs,
)


def test_lambda_handler3(s3_client):
    """Test the lambda_handler function."""
    bucket = "test-data-bucket"

    file_contents = {
        "logs/flight_iois_1.log.2025-212T16_55_27.531613": (
            "Thu Jul 31 16:32:38 UTC 2025\n2025/212-16:32:38.239 some log line\n"
        ),
        "logs/flight_iois_1.log.2025-212T16_56_10.000000": (
            "2025/212-16:33:04.063 another log line\n"
            "2025/212-16:33:06.070 more log lines\n"
        ),
    }

    for key, content in file_contents.items():
        s3_client.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))

    event = {
        "region": "us-west-2",
        "detail": {
            "object": {"key": "packets/file.txt"},
            "bucket": {"name": bucket},
            "now": "2025-07-31T16:57:00Z",
        },
    }

    response = lambda_handler(event, context={})
    assert response["statusCode"] == 204


def test_query_filenames(s3_client):
    """Test the query_filenames function."""
    bucket = "test-data-bucket"
    now = datetime(2025, 7, 31, 16, 55, 28, 531613, tzinfo=timezone.utc)

    inside_range_keys = [
        "logs/flight_iois_1.log.2025-212T16_55_27.531613",
        "logs/flight_iois_1.log.2025-212T15_55_27.531613",
    ]

    outside_range_key = "logs/flight_iois_1.log.2024-212T16_55_27.531613"

    for key in [*inside_range_keys, outside_range_key]:
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"dummy data")

    result = query_filenames(s3_client, bucket, now)

    assert sorted(result) == [
        "flight_iois_1.log.2025-212T15_55_27.531613",
        "flight_iois_1.log.2025-212T16_55_27.531613",
    ]


def test_read_ingest_logs(s3_client):
    """Test the read_ingest_logs function."""
    bucket = "test-data-bucket"
    filenames = ["file1.log", "file2.log"]

    file_contents = {
        "logs/file1.log": (
            "Thu Jul 31 16:32:38 UTC 2025\n2025/212-16:32:38.239 some log line\n"
        ),
        "logs/file2.log": (
            "2025/212-16:33:04.063 another log line\n"
            "2025/212-16:33:06.070 more log lines\n"
        ),
    }

    for key, content in file_contents.items():
        s3_client.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))

    all_lines = read_ingest_logs(s3_client, filenames, bucket)

    assert all_lines == [
        "Thu Jul 31 16:32:38 UTC 2025",
        "2025/212-16:32:38.239 some log line",
        "2025/212-16:33:04.063 another log line",
        "2025/212-16:33:06.070 more log lines",
    ]
