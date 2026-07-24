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


@patch("sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_file")
def test_science_release(mock_download_file, session, tmp_path):
    """Test that science files in the manifest are released properly."""
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
    file_content = """hit, l0, hk, false\nhit, l0, sci, true"""
    manifest_path = tmp_path / "imap_hit_release_20250101_20250131_v001.txt"
    manifest_path.write_text(file_content, encoding="utf-8")
    mock_download_file.return_value = manifest_path

    params = {
        "instrument": "hit",
        "start_date": "20250101",
        "end_date": "20250131",
        "release_type": "release",
        "release_number": "1",
        "manifest_file": "imap_hit_release_20250101_20250131_v001.txt",
    }
    result = release_api.lambda_handler(
        event=_build_event(params),
        context={},
    )

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.ScienceFiles).all()}
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250110_v001.0000.pkts"] is False, (
        "HK descriptor file should remain unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_hk_20250201_v001.0000.pkts"] is False, (
        "HK descriptor file should remain unreleased"
    )
    assert rows["imap/hit/l0/imap_hit_l0_sci_20250120_v001.0000.pkts"] is True, (
        "Sci descriptor file should be released"
    )


@patch("sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_file")
def test_ancillary_release(mock_download_file, session, tmp_path):
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

    result = release_api.latest_ancillary_release(
        session,
        start_date=datetime.datetime.strptime("20260403", "%Y%m%d"),
        end_date=datetime.datetime.strptime("20260430", "%Y%m%d"),
        line="codice,ancillary,l1a-sci-lut,true",
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

    # Date edge cases
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260401_20260413_v001.csv",
        version="v001",
        start_date="20260401",
        end_date="20260413",
    )
    _ancillary(
        session,
        file_path="imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260401_20260413_v002.csv",
        version="v002",
        start_date="20260401",
        end_date="20260413",
    )
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

    file_content = """swe, ancillary, l1b-in-flight-cal, true"""
    manifest_path = tmp_path / "imap_swe_release_20260401_20260430_v001.txt"
    manifest_path.write_text(file_content, encoding="utf-8")
    mock_download_file.return_value = manifest_path

    params = {
        "instrument": "swe",
        "start_date": "20260401",
        "end_date": "20260430",
        "release_type": "release",
        "release_number": "1",
        "manifest_file": "s3://dummy-bucket/imap_swe_release_20260401_20260430_v001.txt",
    }
    result = release_api.lambda_handler(event=_build_event(params), context={})

    assert result["statusCode"] == 200

    rows = {r.file_path: r.released for r in session.query(models.AncillaryFiles).all()}
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260401_20260413_v001.csv"]
        is False
    ), "Older version should not be released"
    # Latest version should be released
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260401_20260413_v002.csv"]
        is True
    ), "Latest version should be released"
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v001.csv"] is False
    ), "Excluded file should not be released"
    # Latest version should be released
    assert (
        rows["imap/ancillary/swe/imap_swe_l1b-in-flight-cal_20260420_v002.csv"] is True
    ), "Latest version for April 20 should be released"


@patch("sds_data_manager.lambda_code.SDSCode.api_lambdas.release_api.download_file")
def test_ancillary_release_with_wildcard(mock_download_file, session, tmp_path):
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

    file_content = """swe, ancillary, *, true"""
    manifest_path = tmp_path / "imap_swe_release_20260401_20260430_v001.txt"
    manifest_path.write_text(file_content, encoding="utf-8")
    mock_download_file.return_value = manifest_path

    # No exclude file provided
    params = {
        "instrument": "swe",
        "start_date": "20260401",
        "end_date": "20260430",
        "release_type": "release",
        "release_number": "1",
        "manifest_file": "s3://dummy-bucket/imap_swe_release_20260401_20260430_v001.txt",
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


def test_early_release():
    result = release_api.lambda_handler(
        event=_build_event(
            {
                "release_type": "early-release",
                "manifest_file": "s3://dummy-bucket/manifest.txt",
            }
        ),
        context={},
    )

    assert result["statusCode"] == 501
    assert result["body"] == "Early release operation not supported yet."


def test_unrelease_all_files_in_date_range():
    result = release_api.lambda_handler(
        event=_build_event(
            {
                "release_type": "unrelease",
                "manifest_file": "s3://dummy-bucket/manifest.txt",
            }
        ),
        context={},
    )

    assert result["statusCode"] == 501
    assert result["body"] == "Unrelease operation not supported yet."


def test_latest_science_release(session):
    """Test multiple repoint files for a single day."""
    # April 7th, Hi has three repoint files with different repointing and major
    # /minor versions
    files = [
        ("imap_hi_l1a_45sensor-hk_20260407-repoint00209_v001.0003.cdf", 209, 1, 3),
        ("imap_hi_l1a_45sensor-hk_20260407-repoint00210_v001.0001.cdf", 210, 1, 1),
        ("imap_hi_l1a_45sensor-hk_20260407-repoint00211_v001.0001.cdf", 211, 1, 1),
        ("imap_hi_l1a_45sensor-hk_20260407-repoint00211_v001.0002.cdf", 211, 1, 2),
    ]
    for file_path, repointing, major_ver, minor_ver in files:
        session.add(
            models.ScienceFiles(
                file_path=file_path,
                instrument="hi",
                data_level="l1a",
                descriptor="45sensor-hk",
                start_date=datetime.datetime.strptime("20260407", "%Y%m%d"),
                repointing=repointing,
                major_version=major_ver,
                minor_version=minor_ver,
                extension="cdf",
                released=False,
                ingestion_date=datetime.datetime(2026, 4, 8, 0, 0, 0),
            )
        )
    session.commit()

    # Query for all files on this date
    results = release_api.latest_science_release(
        session,
        start_date=datetime.datetime.strptime("20260407", "%Y%m%d"),
        end_date=datetime.datetime.strptime("20260407", "%Y%m%d"),
        line="hi, l1a, *, true",
    )
    file_paths = sorted([obj.file_path for obj in results])
    assert file_paths == [
        "imap_hi_l1a_45sensor-hk_20260407-repoint00209_v001.0003.cdf",
        "imap_hi_l1a_45sensor-hk_20260407-repoint00210_v001.0001.cdf",
        "imap_hi_l1a_45sensor-hk_20260407-repoint00211_v001.0002.cdf",
    ], f"Expected all repoint files, got: {file_paths}"

    # Query non-repoint files
    files = [
        ("imap_swapi_l1_sci_20260407_v002.0002.cdf", "sci", "20260407", 2, 2),
        ("imap_swapi_l1_sci_20260407_v001.0002.cdf", "sci", "20260407", 1, 2),
        ("imap_swapi_l1_sci_20260407_v001.0001.cdf", "sci", "20260407", 1, 1),
        ("imap_swapi_l1_sci_20260408_v001.0001.cdf", "sci", "20260408", 1, 1),
        ("imap_swapi_l1_sci_20260408_v001.0002.cdf", "sci", "20260408", 1, 2),
        # HK is used to see if it gets excluded properly in later step
        ("imap_swapi_l1a_hk_20260408_v001.0001.cdf", "hk", "20260408", 1, 1),
    ]
    for file_path, descriptor, start_date, major_ver, minor_ver in files:
        session.add(
            models.ScienceFiles(
                file_path=file_path,
                instrument="swapi",
                data_level="l1",
                descriptor=descriptor,
                start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
                repointing=None,
                major_version=major_ver,
                minor_version=minor_ver,
                extension="cdf",
                released=False,
                ingestion_date=datetime.datetime(2026, 4, 8, 0, 0, 0),
            )
        )
    session.commit()

    latest_non_repoint_files = release_api.latest_science_release(
        session,
        start_date=datetime.datetime.strptime("20260407", "%Y%m%d"),
        end_date=datetime.datetime.strptime("20260407", "%Y%m%d"),
        line="swapi,*,*,true",
    )
    file_paths = sorted([obj.file_path for obj in latest_non_repoint_files])
    assert file_paths == [
        "imap_swapi_l1_sci_20260407_v002.0002.cdf",
    ], f"Expected only the latest non-repoint file, got: {file_paths}"

    # In this release query, we only ask for latest sci files on April 8th,
    # so the HK file should be excluded and should not be returned.
    latest_non_repoint_files = release_api.latest_science_release(
        session,
        start_date=datetime.datetime.strptime("20260408", "%Y%m%d"),
        end_date=datetime.datetime.strptime("20260408", "%Y%m%d"),
        line="swapi,l1,sci,true",
    )
    file_paths = sorted([obj.file_path for obj in latest_non_repoint_files])
    assert file_paths == [
        "imap_swapi_l1_sci_20260408_v001.0002.cdf",
    ], f"Expected only the latest non-repoint file, got: {file_paths}"
