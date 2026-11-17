"""Tests for the script that copies released files to the public bucket."""

from datetime import datetime

import boto3
import pytest
from moto import mock_s3

from scripts.release import copy_released_files_to_public_bucket as copy_script
from sds_data_manager.lambda_code.SDSCode.database import models
from tests.conftest import in_memory_session

SOURCE_BUCKET = "test-data-bucket"
DEST_BUCKET = "test-public-data-bucket"
RELEASED_V1 = "imap/hit/l2/2026/02/imap_hit_l2_summed-intensity_20260201_v001.0001.cdf"
RELEASED_V2 = "imap/hit/l2/2026/02/imap_hit_l2_summed-intensity_20260201_v002.0001.cdf"
UNRELEASED = "imap/hit/l2/2026/02/imap_hit_l2_summed-intensity_20260202_v002.0001.cdf"
ANCILLARY = "imap/ancillary/hit/imap_hit_l1b-cal_20260101_v001.cdf"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Use fake AWS credentials for the mocked buckets."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def s3_client():
    """Mock S3 with a data bucket holding every file and an empty public bucket."""
    with mock_s3():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=SOURCE_BUCKET)
        client.create_bucket(Bucket=DEST_BUCKET)
        for key in (RELEASED_V1, RELEASED_V2, UNRELEASED, ANCILLARY):
            client.put_object(Bucket=SOURCE_BUCKET, Key=key, Body=key.encode())
        yield client


@pytest.fixture
def session():
    """Create a test database with released and unreleased files."""
    with in_memory_session() as session:
        for file_path, major_version, start_date, released in (
            (RELEASED_V1, 1, "20260201", True),
            (RELEASED_V2, 2, "20260201", True),
            (UNRELEASED, 2, "20260202", False),
        ):
            session.add(
                models.ScienceFiles(
                    file_path=file_path,
                    instrument="hit",
                    data_level="l2",
                    descriptor="summed-intensity",
                    start_date=datetime.strptime(start_date, "%Y%m%d"),
                    major_version=major_version,
                    minor_version=1,
                    extension="cdf",
                    released=released,
                )
            )
        session.add(
            models.AncillaryFiles(
                file_path=ANCILLARY,
                instrument="hit",
                descriptor="l1b-cal",
                start_date=datetime(2026, 1, 1),
                version="v001",
                extension="cdf",
            )
        )
        session.commit()
        yield session


def _public_keys(s3_client):
    return copy_script.list_bucket_keys(s3_client, DEST_BUCKET)


def _copy(**kwargs):
    return copy_script.copy_released_files(
        source_bucket=SOURCE_BUCKET, dest_bucket=DEST_BUCKET, **kwargs
    )


def test_copies_every_released_science_version(session, s3_client):
    """Copy all released science versions, but not unreleased or ancillary files."""
    summary = _copy(table_names=["science"])

    assert summary.copied == [RELEASED_V1, RELEASED_V2]
    assert not summary.failures
    assert _public_keys(s3_client) == {RELEASED_V1, RELEASED_V2}
    body = s3_client.get_object(Bucket=DEST_BUCKET, Key=RELEASED_V2)["Body"].read()
    assert body == RELEASED_V2.encode()


def test_includes_ancillary_when_requested(session, s3_client):
    """Copy released ancillary files along with science files."""
    summary = _copy(table_names=["science", "ancillary"])

    assert summary.copied == [ANCILLARY, RELEASED_V1, RELEASED_V2]
    assert _public_keys(s3_client) == {ANCILLARY, RELEASED_V1, RELEASED_V2}


def test_skips_files_already_public(session, s3_client):
    """Leave files already in the public bucket untouched."""
    s3_client.put_object(Bucket=DEST_BUCKET, Key=RELEASED_V1, Body=b"existing")

    summary = _copy(table_names=["science"])

    assert summary.released == 2
    assert summary.already_public == 1
    assert summary.copied == [RELEASED_V2]
    body = s3_client.get_object(Bucket=DEST_BUCKET, Key=RELEASED_V1)["Body"].read()
    assert body == b"existing"


def test_dry_run_copies_nothing(session, s3_client):
    """List the files to copy without copying them."""
    summary = _copy(table_names=["science"], dry_run=True)

    assert summary.copied == [RELEASED_V1, RELEASED_V2]
    assert _public_keys(s3_client) == set()


def test_reports_files_missing_from_source(session, s3_client):
    """Report a released file missing from the data bucket and copy the rest."""
    s3_client.delete_object(Bucket=SOURCE_BUCKET, Key=RELEASED_V1)

    summary = _copy(table_names=["science"])

    assert summary.copied == [RELEASED_V2]
    assert list(summary.failures) == [RELEASED_V1]


def test_main_exit_status(session, s3_client, capsys):
    """Exit 0 when every copy succeeds and 1 when any fails."""
    args = ["--source-bucket", SOURCE_BUCKET, "--dest-bucket", DEST_BUCKET]
    s3_client.delete_object(Bucket=SOURCE_BUCKET, Key=RELEASED_V1)

    assert copy_script.main(args) == 1
    assert f"FAILED {RELEASED_V1}" in capsys.readouterr().out

    s3_client.put_object(Bucket=SOURCE_BUCKET, Key=RELEASED_V1, Body=b"restored")
    assert copy_script.main(args) == 0
    assert _public_keys(s3_client) == {RELEASED_V1, RELEASED_V2}
