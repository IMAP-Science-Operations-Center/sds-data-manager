"""Tests for the I-ALiRT Archive Query API."""

import json

import pytest

from sds_data_manager.lambda_code.IAlirtCode import ialirt_archive_query_api

BUCKET = "test-data-bucket"

ARCHIVE_FILES = [
    "archive/imap_ialirt_l1_realtime_20240521_v001.cdf",
    "archive/imap_ialirt_l1_realtime_20240522_v001.cdf",
    "archive/imap_ialirt_l1_realtime_20240601_v001.cdf",
    "archive/imap_ialirt_l1_realtime_20240521_v002.cdf",
]


@pytest.fixture
def populated_bucket(s3_client):
    """Upload archive test files to the mocked S3 bucket."""
    for key in ARCHIVE_FILES:
        s3_client.put_object(Bucket=BUCKET, Key=key, Body=b"test-data")
    return s3_client


def test_archive_query_no_params(populated_bucket, monkeypatch):
    """No params returns all v001 files."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)
    files = json.loads(response["body"])["files"]

    assert response["statusCode"] == 200
    assert files == [
        "imap_ialirt_l1_realtime_20240521_v001.cdf",
        "imap_ialirt_l1_realtime_20240522_v001.cdf",
        "imap_ialirt_l1_realtime_20240601_v001.cdf",
    ]


def test_archive_query_by_year(populated_bucket, monkeypatch):
    """Filtering by year returns all v001 files for that year."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"year": "2024"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)
    files = json.loads(response["body"])["files"]

    assert response["statusCode"] == 200
    assert files == [
        "imap_ialirt_l1_realtime_20240521_v001.cdf",
        "imap_ialirt_l1_realtime_20240522_v001.cdf",
        "imap_ialirt_l1_realtime_20240601_v001.cdf",
    ]


def test_archive_query_by_year_month(populated_bucket, monkeypatch):
    """Filtering by year and month narrows results to that month."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"year": "2024", "month": "05"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)
    files = json.loads(response["body"])["files"]

    assert response["statusCode"] == 200
    assert files == [
        "imap_ialirt_l1_realtime_20240521_v001.cdf",
        "imap_ialirt_l1_realtime_20240522_v001.cdf",
    ]


def test_archive_query_by_full_date(populated_bucket, monkeypatch):
    """Filtering by year, month, and day returns only that day's file."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"year": "2024", "month": "05", "day": "21"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)
    files = json.loads(response["body"])["files"]

    assert response["statusCode"] == 200
    assert files == ["imap_ialirt_l1_realtime_20240521_v001.cdf"]


def test_archive_query_by_version(populated_bucket, monkeypatch):
    """Filtering by version returns only files with that version."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"year": "2024", "month": "05", "version": "2"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)
    files = json.loads(response["body"])["files"]

    assert response["statusCode"] == 200
    assert files == ["imap_ialirt_l1_realtime_20240521_v002.cdf"]


def test_archive_query_day_without_month(monkeypatch):
    """Specifying day without month returns a 400 error."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"year": "2024", "day": "21"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "order" in response["body"]


def test_archive_query_month_without_year(monkeypatch):
    """Specifying month without year returns a 400 error."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"month": "05"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "order" in response["body"]


def test_archive_query_invalid_version(monkeypatch):
    """A non-integer version returns a 400 error."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"version": "abc"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "version" in response["body"].lower()


def test_archive_query_since(populated_bucket, monkeypatch):
    """Since returns all v001 files with a date on or after the given date."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"since": "20240522"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)
    files = json.loads(response["body"])["files"]

    assert response["statusCode"] == 200
    assert files == [
        "imap_ialirt_l1_realtime_20240522_v001.cdf",
        "imap_ialirt_l1_realtime_20240601_v001.cdf",
    ]


def test_archive_query_since_invalid_format(monkeypatch):
    """A malformed since value returns a 400 error."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"since": "2024-05-21"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "since" in response["body"].lower()


def test_archive_query_since_with_year_returns_400(monkeypatch):
    """Combining since with year/month/day returns a 400 error."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("REGION", "us-east-1")

    event = {"queryStringParameters": {"since": "20240521", "year": "2024"}}
    response = ialirt_archive_query_api.lambda_handler(event=event, context=None)

    assert response["statusCode"] == 400
    assert "since" in response["body"].lower()
