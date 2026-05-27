"""Integration tests for the Release API.

These tests verify the six core release behaviors against a live in-memory DB:

Test Case 1  release (no descriptor)  — all matching instrument+date files released
Test Case 2  early-release
Test Case 3  unrelease
"""

import datetime
from unittest.mock import patch

from sds_data_manager.lambda_code.SDSCode.api_lambdas import release_api
from sds_data_manager.lambda_code.SDSCode.database import models

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_event(query_params, scope="full"):
    """Build a minimal API Gateway event for the release API."""
    return {
        "queryStringParameters": query_params,
        "rawPath": "/api-key/release",
        "body": "",
        "requestContext": {
            "authorizer": {"lambda": {"apiKey": "test-key", "scope": scope}}
        },
    }


def _science(
    session,
    *,
    file_path,
    instrument="hit",
    descriptor="hk",
    start_date="20250115",
    released=False,
):
    session.add(
        models.ScienceFiles(
            file_path=file_path,
            instrument=instrument,
            data_level="l0",
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            version="v001",
            extension="pkts",
            released=released,
            ingestion_date=datetime.datetime(
                2025, 1, 20, 0, 0, 0, tzinfo=datetime.timezone.utc
            ),
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# Test Case 1  release — all files for instrument + date range
# ---------------------------------------------------------------------------


def test_release_all_files_in_date_range(session):
    """release_type=release with no descriptor releases every matching file.

    Two files for the target instrument within the date range and one file
    outside the range must remain unreleased.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
    )  # outside range

    params = {
        "instrument": "hit",
        "start_date": "20250101",
        "end_date": "20250131",
        "release_type": "release",
        "release_number": "1",
    }
    result = release_api.lambda_handler(
        event=_build_event(params),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts"] is True, (
        "In-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts"] is True, (
        "In-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts"] is False, (
        "Out-of-range file must stay unreleased"
    )


# ---------------------------------------------------------------------------
# Test Case 2  early-release
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_early_release(mock_download_read_file, session):
    # Provide the manifest file with the two in-range files
    science_files = [
        "imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        "imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
    ]
    ancillary_files = []
    mock_download_read_file.return_value = (science_files, ancillary_files)

    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
    )

    manifest_file = "s3://dummy-bucket/manifest.txt"
    result = release_api.lambda_handler(
        event=_build_event(
            {
                "release_type": "early-release",
                "manifest_file": manifest_file,
            }
        ),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts"] is False


# ---------------------------------------------------------------------------
# Test Case 3  unrelease
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_unrelease_all_files_in_date_range(mock_download_read_file, session):
    # Provide the manifest file with the two in-range files
    science_files = [
        "imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        "imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
    ]
    ancillary_files = []
    mock_download_read_file.return_value = (science_files, ancillary_files)

    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
        released=True,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
        released=True,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
        released=True,
    )  # outside range

    manifest_file = "s3://dummy-bucket/manifest.txt"
    result = release_api.lambda_handler(
        event=_build_event(
            {
                "release_type": "unrelease",
                "manifest_file": manifest_file,
            }
        ),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.pkts"] is False, (
        "In-range file must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.pkts"] is False, (
        "In-range file must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.pkts"] is True, (
        "Out-of-range file must remain released"
    )
