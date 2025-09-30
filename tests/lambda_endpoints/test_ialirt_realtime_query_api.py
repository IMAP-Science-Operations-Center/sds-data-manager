"""Tests for the I-ALiRT Realtime Query API."""

import json
from datetime import datetime, timedelta, timezone

from sds_data_manager.lambda_code.IAlirtCode import ialirt_realtime_query_api


def test_realtime_query_returns_latest_file(s3_client):
    """Test that the realtime query API returns the latest realtime file."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    now = datetime.now(timezone.utc)
    prefix = now.strftime("realtime/imap_ialirt_realtime_%Y-%jT%H")

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=f"{prefix}:{now.minute:02d}.json",
        Body=b"older file",
    )

    # Newer file, 1 minute later
    newer_time = now + timedelta(minutes=1)
    newer_key = f"{prefix}:{newer_time.minute:02d}.json"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=newer_key,
        Body=b"newer file",
    )

    response = ialirt_realtime_query_api.lambda_handler(event={}, context=None)
    response_data = json.loads(response["body"])

    # Check that the status and filename are correct
    assert response["statusCode"] == 200
    assert (
        response_data["latest_file"]
        == f"realtime/{prefix}:{newer_time.minute:02d}.json"
    )


def test_realtime_query_no_files(monkeypatch, s3_client):
    """Test that the realtime query API returns 404 when no files exist."""
    bucket = "test-empty-bucket"
    s3_client.create_bucket(Bucket=bucket)

    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setenv("REGION", "us-west-2")

    response = ialirt_realtime_query_api.lambda_handler(event={}, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert "No realtime files found" in response_data["error"]
