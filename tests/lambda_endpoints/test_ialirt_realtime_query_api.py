"""Tests for the I-ALiRT Realtime Query API."""

import json
import time
from datetime import datetime, timedelta, timezone

from sds_data_manager.lambda_code.IAlirtCode import ialirt_realtime_query_api


def test_realtime_query_returns_latest_file(s3_client):
    """Test realtime query API."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    now = datetime.now(timezone.utc)
    prefix = now.strftime("realtime/imap_ialirt_realtime_%Y-%jT%H")

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=f"{prefix}:{now.second:02d}.json",
        Body=b"older file",
    )
    time.sleep(1)

    newer_time = now + timedelta(seconds=1)
    newer_key = f"{prefix}:{newer_time.second:02d}.json"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=newer_key,
        Body=b"newer file",
    )

    response = ialirt_realtime_query_api.lambda_handler(event={}, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert (
        f"realtime/{response_data['latest_file']}"
        == f"{prefix}:{newer_time.second:02d}.json"
    )


def test_realtime_query_no_files(s3_client):
    """Test realtime query API with no files."""
    s3_client.create_bucket(Bucket="test-data-bucket")

    response = ialirt_realtime_query_api.lambda_handler(event={}, context=None)
    response_data = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert "No realtime files found" in response_data["error"]
