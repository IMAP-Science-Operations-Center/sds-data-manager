"""Integration tests for the Release API."""

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
    major_version=1,
    minor_version=1,
):
    session.add(
        models.ScienceFiles(
            file_path=file_path,
            instrument=instrument,
            data_level="l0",
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            major_version=major_version,
            minor_version=minor_version,
            extension="pkts",
            released=released,
            ingestion_date=datetime.datetime(
                2025, 1, 20, 0, 0, 0, tzinfo=datetime.timezone.utc
            ),
        )
    )
    session.commit()


def _ancillary(
    session,
    *,
    file_path,
    instrument="swe",
    descriptor="l1b-in-flight-cal",
    start_date="20260413",
    end_date=None,
    version="v001",
    released=False,
):
    """Insert a single AncillaryFiles row and commit."""
    session.add(
        models.AncillaryFiles(
            file_path=file_path,
            instrument=instrument,
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            end_date=(
                datetime.datetime.strptime(end_date, "%Y%m%d") if end_date else None
            ),
            version=version,
            extension="csv",
            released=released,
            ingestion_date=datetime.datetime(2026, 4, 14, 0, 0, 0),
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
#   release — all files for instrument + date range + release number
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_release_all_files_in_date_range(mock_download_read_file, session):
    """release_type=release with no descriptor releases every matching file.

    Two files for the target instrument within the date range and one file
    outside the range must remain unreleased.
    """
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v001.0000.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
        major_version=1,
        minor_version=0,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v001.0000.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
        major_version=1,
        minor_version=0,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v001.0000.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
        major_version=1,
        minor_version=0,
    )  # outside range

    # Provide the manifest file with the two in-range files
    science_files = [
        "imap/hit/l0/imap_hit_l0_hk_20250110_v001.0000.pkts",
    ]
    ancillary_files = []
    mock_download_read_file.return_value = (science_files, ancillary_files)

    params = {
        "instrument": "hit",
        "start_date": "20250101",
        "end_date": "20250131",
        "release_type": "release",
        "release_number": "1",
        "exclude_file": "some_file.txt",
    }
    result = release_api.lambda_handler(
        event=_build_event(params),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.0000.pkts"] is False, (
        "Excluded in-range file should not be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.0000.pkts"] is True, (
        "Non-excluded in-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.0000.pkts"] is False, (
        "Out-of-range file must stay unreleased"
    )

    # Now test without exclude file.
    params.pop("exclude_file")
    result = release_api.lambda_handler(
        event=_build_event(params),
        context={},
    )
    assert result["statusCode"] == 200
    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.0000.pkts"] is True, (
        "In-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.0000.pkts"] is True, (
        "In-range file should be released"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.0000.pkts"] is False, (
        "Out-of-range file must stay unreleased"
    )


# ---------------------------------------------------------------------------
#  ancillary files release
# ---------------------------------------------------------------------------
def test_release_ancillary_files_in_date_range(session):
    """Ancillary files in manifest are released properly."""
    # Add all as unreleased
    session.add(
        models.AncillaryFiles(
            file_path="imap/ancillary/codice/imap_codice_l1a-sci-lut_20260129_v002.json",
            instrument="codice",
            descriptor="l1a-sci-lut",
            start_date=datetime.datetime.strptime("20260129", "%Y%m%d"),
            end_date=None,
            version="v002",
            extension="json",
            released=False,
            ingestion_date=datetime.datetime(2026, 1, 30, 0, 0, 0),
        )
    )
    session.add(
        models.AncillaryFiles(
            file_path="imap/ancillary/codice/imap_codice_l1a-sci-lut_20260403_20260403_v001.json",
            instrument="codice",
            descriptor="l1a-sci-lut",
            start_date=datetime.datetime.strptime("20260403", "%Y%m%d"),
            end_date=datetime.datetime.strptime("20260403", "%Y%m%d"),
            version="v001",
            extension="json",
            released=False,
            ingestion_date=datetime.datetime(2026, 4, 4, 0, 0, 0),
        )
    )
    # Add an out-of-range ancillary file (should not be released)
    session.add(
        models.AncillaryFiles(
            file_path="imap/ancillary/codice/imap_codice_l1a-sci-lut_20260403_v001.json",
            instrument="codice",
            descriptor="l1a-sci-lut",
            start_date=datetime.datetime.strptime("20260403", "%Y%m%d"),
            end_date=None,
            version="v001",
            extension="json",
            released=False,
            ingestion_date=datetime.datetime(2026, 1, 2, 0, 0, 0),
        )
    )
    session.add(
        models.AncillaryFiles(
            file_path="imap/ancillary/codice/imap_codice_l1a-sci-lut_20260403_v002.json",
            instrument="codice",
            descriptor="l1a-sci-lut",
            start_date=datetime.datetime.strptime("20260403", "%Y%m%d"),
            end_date=None,
            version="v002",
            extension="json",
            released=False,
            ingestion_date=datetime.datetime(2026, 1, 2, 0, 0, 0),
        )
    )
    session.commit()

    result = release_api.get_latest_ancillary_files(
        session,
        instrument="codice",
        start_date=datetime.datetime.strptime("20260403", "%Y%m%d"),
        end_date=datetime.datetime.strptime("20260430", "%Y%m%d"),
    )
    expected_ancillary_files = [
        "imap/ancillary/codice/imap_codice_l1a-sci-lut_20260403_20260403_v001.json",
        "imap/ancillary/codice/imap_codice_l1a-sci-lut_20260403_v002.json",
    ]
    assert len(result) == 2, f"Expected 2 ancillary files, got {len(result)}"
    assert sorted([f.file_path for f in result]) == expected_ancillary_files, (
        f"Expected ancillary files {expected_ancillary_files}, "
        f"got {[f.file_path for f in result]}"
    )


# ---------------------------------------------------------------------------
# ancillary release with exclude file
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_ancillary_release_with_exclude_file(mock_download_read_file, session):
    """release_type=release excludes specified ancillary files.

    Three ancillary files for swe/l1b-in-flight-cal:
      - v001 (older version, same descriptor+date) — should stay unreleased because
        v002 is the latest.
      - v002 (latest version, in range) — should be released because it is the
        latest and is NOT in the exclude list.
      - v001 with a different start_date inside range, but listed in the exclude
        file — must remain unreleased.
    """
    # Older version — superseded by v002, same start_date
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v001.csv",
        version="v001",
        start_date="20260413",
    )
    # Latest version — should be released
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v002.csv",
        version="v002",
        start_date="20260413",
    )
    # In-range file that is the only version for its date but is in the exclude list
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v001.csv",
        version="v001",
        start_date="20260420",
    )
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v002.csv",
        version="v002",
        start_date="20260420",
    )

    # Exclude list contains the April 20 file
    mock_download_read_file.return_value = (
        [],  # no science files excluded
        ["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v002.csv"],
    )

    params = {
        "instrument": "swe",
        "start_date": "20260401",
        "end_date": "20260430",
        "release_type": "release",
        "release_number": "1",
        "exclude_file": "s3://dummy-bucket/exclude.txt",
    }
    result = release_api.lambda_handler(event=_build_event(params), context={})

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.AncillaryFiles).all()}
    # Older version: not selected as latest → stays unreleased
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v001.csv"] is False
    ), "Older version should not be released"
    # Latest version, not excluded → should be released
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v002.csv"] is True
    ), "Latest version should be released"
    # In-range but explicitly excluded → must stay unreleased
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v001.csv"] is False
    ), "Excluded file should not be released"
    # Latest version for April 20 -> must stay unreleased
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v002.csv"] is False
    ), "Latest version for April 20 should not be released"


# ---------------------------------------------------------------------------
#  ancillary release without exclude file — only latest version released
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_ancillary_release_without_exclude_file_latest_version_only(
    mock_download_read_file, session
):
    """release_type=release with no exclude file releases only the latest version.

    Two versions of the same descriptor+start_date exist. Only the highest
    version (v002) should be released; v001 must remain unreleased.
    An out-of-range file must also remain unreleased.
    """
    # v001 — older version for April 13
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v001.csv",
        version="v001",
        start_date="20260413",
    )
    # v002 — latest version for April 13
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v002.csv",
        version="v002",
        start_date="20260413",
    )
    # Out-of-range: May 5 — outside the query window
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260505_v001.csv",
        version="v001",
        start_date="20260505",
    )

    # No exclude file provided
    params = {
        "instrument": "swe",
        "start_date": "20260401",
        "end_date": "20260430",
        "release_type": "release",
        "release_number": "1",
    }
    result = release_api.lambda_handler(event=_build_event(params), context={})

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.AncillaryFiles).all()}
    # Older version should remain unreleased
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v001.csv"] is False
    ), "Older version must not be released"
    # Latest version should be released
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260413_v002.csv"] is True
    ), "Latest version should be released"
    # Out-of-range file must stay unreleased
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260505_v001.csv"] is False
    ), "Out-of-range file must not be released"


# ---------------------------------------------------------------------------
#   early-release
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_early_release(mock_download_read_file, session):
    # Provide the manifest file with the two in-range files
    science_files = [
        "imap/hit/l0/imap_hit_l0_hk_20250110_v000.0001.pkts",
        "imap/hit/l0/imap_hit_l0_sci_20250120_v000.0001.pkts",
    ]
    ancillary_files = []
    mock_download_read_file.return_value = (science_files, ancillary_files)

    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v000.0001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
        major_version=0,
        minor_version=1,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v000.0001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
        major_version=0,
        minor_version=1,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v000.0001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
        major_version=0,
        minor_version=1,
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
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v000.0001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v000.0001.pkts"] is True
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v000.0001.pkts"] is False


# ---------------------------------------------------------------------------
#   unrelease
# ---------------------------------------------------------------------------


@patch(
    "sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_read_file"
)
def test_unrelease_all_files_in_date_range(mock_download_read_file, session):
    # Provide the manifest file with the two in-range files
    science_files = [
        "imap/hit/l0/imap_hit_l0_hk_20250110_v000.0001.pkts",
        "imap/hit/l0/imap_hit_l0_sci_20250120_v000.0001.pkts",
    ]
    ancillary_files = []
    mock_download_read_file.return_value = (science_files, ancillary_files)

    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250110_v000.0001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250110",
        released=True,
        major_version=0,
        minor_version=1,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_sci_20250120_v000.0001.pkts",
        instrument="hit",
        descriptor="sci",
        start_date="20250120",
        released=True,
        major_version=0,
        minor_version=1,
    )
    _science(
        session,
        file_path="imap/hit/l0/imap_hit_l0_hk_20250201_v000.0001.pkts",
        instrument="hit",
        descriptor="hk",
        start_date="20250201",
        released=True,
        major_version=0,
        minor_version=1,
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
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v000.0001.pkts"] is False, (
        "In-range file must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v000.0001.pkts"] is False, (
        "In-range file must be unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v000.0001.pkts"] is True, (
        "Out-of-range file must remain released"
    )

