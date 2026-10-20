"""Tests for the SPICE indexer lambda."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import imap_data_access
import numpy as np
import pytest
import spiceypy
from imap_data_access import SPICEFilePath
from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.api_lambdas import (
    spice_metakernel_api,
    spice_query_api,
)
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer import (
    MAXIMUM_J2000_INTERVAL,
    MAXIMUM_SCLK_INTERVAL,
    clear_ephemeral_storage,
    get_coverage_dictionary,
    get_small_forces_file_coverage,
    get_spin_file_coverage,
    index_pointing_data,
    index_small_forces_file,
    index_spice_file,
    parse_datetime,
)

TEST_SPICE_DATA_DIR = Path(__file__).parent.parent / "test-data" / "test_spice_files"

# Real coverage of the spin and small forces test files, from their rows
SPIN_COVERAGE = {
    "imap_2026_267_2026_267_01.spin.csv": (
        datetime(2026, 9, 24, 14, 1, 41),
        datetime(2026, 9, 24, 16, 41, 2),
    ),
}
SFF_COVERAGE = {
    "imap_2026_273_2026_273_hist_01.sff": (
        datetime(2026, 9, 30, 13, 2, 6, 662376),
        datetime(2026, 9, 30, 19, 11, 25, 643765),
    ),
    "imap_2026_273_2026_273_hist_02.sff": (
        datetime(2026, 9, 30, 13, 2, 7, 662385),
        datetime(2026, 9, 30, 19, 11, 25, 643780),
    ),
    "imap_2026_273_2026_274_hist_01.sff": (
        datetime(2026, 9, 30, 19, 11, 25, 643780),
        datetime(2026, 10, 1, 19, 11, 24, 571223),
    ),
}


def put_local_file_in_bucket(s3_client, path_in_s3, path_local):
    """Put the a local file into a test bucket, and return a mock event notification."""
    # Ensure the correct bucket name is used from environment or fallback
    bucket_name = os.getenv("S3_BUCKET")
    with open(path_local, "rb") as f:
        s3_client.put_object(
            Bucket=bucket_name,
            Key=path_in_s3,
            Body=f,
        )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": datetime.now().isoformat(),
        "detail": {
            "version": "0",
            "bucket": {"name": bucket_name},
            "object": {
                "key": (path_in_s3),
                "reason": "PutObject",
            },
        },
    }
    return event


def _irrelevant_data():
    """Populate irrelevant columns in DB with dummy data."""
    irrelevant_data = {
        "min_date_datetime": datetime.now(),
        "max_date_datetime": datetime.now(),
        "file_intervals_datetime": [["0", "0"]],
        "min_date_sclk": "",
        "max_date_sclk": "",
        "file_intervals_sclk": [["0", "0"]],
        "sclk_kernel": "nothing",
        "lsk_kernel": "nothing",
    }
    return irrelevant_data


def _insert_test_file(session, filename, s3_path, intervals, upload_time=0):
    spice_object = SPICEFilePath(filename)
    version = spice_object.spice_metadata["version"]
    metadata_params = {
        "file_name": filename,
        "file_path": s3_path,
        "file_root": "".join(filename.rsplit(version, 1)),
        "kernel_type": spice_object.spice_metadata["type"],
        "version": version,
        "file_intervals_j2000": intervals,
        "min_date_j2000": intervals[0][0],
        "max_date_j2000": intervals[-1][1],
        "ingestion_date": datetime.now() + timedelta(upload_time),
    } | _irrelevant_data()
    session.add(models.SPICEFiles(**metadata_params))
    session.commit()


@pytest.fixture
def time_kernels(session, s3_client):
    """Make the leapseconds and spacecraft clock kernels available to furnish."""
    for filename, s3_path in [
        ("naif0012.tls", "imap/spice/lsk/naif0012.tls"),
        ("imap_sclk_0012.tsc", "imap/spice/sclk/imap_sclk_0012.tsc"),
    ]:
        put_local_file_in_bucket(s3_client, s3_path, TEST_SPICE_DATA_DIR / filename)
        _insert_test_file(session, filename, s3_path, [[0, 1000000000]])


def _spice_table_rows(session, kernel_type):
    """Return the SPICE table rows of one kernel type, ordered by file name."""
    query = (
        select(models.SPICEFiles)
        .where(models.SPICEFiles.kernel_type == kernel_type)
        .order_by(models.SPICEFiles.file_name)
    )
    return session.execute(query).scalars().all()


def _assert_coverage(row, expected_start, expected_end):
    """Check a SPICE table row's coverage in datetime and J2000 time."""
    # SQLite drops the timezone, so compare as naive UTC datetimes
    assert row.min_date_datetime.replace(tzinfo=None) == expected_start
    assert row.max_date_datetime.replace(tzinfo=None) == expected_end
    assert row.min_date_j2000 == pytest.approx(spiceypy.datetime2et(expected_start))
    assert row.max_date_j2000 == pytest.approx(spiceypy.datetime2et(expected_end))
    assert row.file_intervals_j2000 == [[row.min_date_j2000, row.max_date_j2000]]
    # The time kernels used for the conversions are recorded in their own columns
    assert Path(row.sclk_kernel).name == "imap_sclk_0012.tsc"
    assert Path(row.lsk_kernel).name == "naif0012.tls"


@pytest.mark.parametrize(
    "coverage_file, expected_coverage",  # noqa: PT006
    [
        (
            "imap_2025_118_2025_120_001.ah.bc",
            np.array(
                [
                    [799244416.073258, 799244763.0732579],
                ]
            ),
        ),
        (
            "imap_dps_2025_284_2025_285_001.ah.bc",
            np.array(
                [
                    [784909316.4208736, 784995503.4208926],
                ]
            ),
        ),
    ],
)
def test_get_coverage_dictionary(coverage_file, expected_coverage):
    """Test get_coverage_dictionary for various files."""
    tests_path = Path(os.path.abspath(__file__)).parent.parent
    test_spice_data_dir = tests_path / "test-data" / "test_spice_files"
    with spiceypy.KernelPool(
        [
            str(test_spice_data_dir / "naif0012.tls"),
            str(test_spice_data_dir / "imap_sclk_0012.tsc"),
        ]
    ):
        results_j2000, _results_datetime, _results_sclk = get_coverage_dictionary(
            test_spice_data_dir / coverage_file
        )
        np.testing.assert_array_equal(results_j2000, expected_coverage)


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.wnfetd"
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.wncard"
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.pckcov"
)
def test_get_coverage_dictionary_left_out_of_range(
    mock_pckcov, mock_wncard, mock_wnfetd
):
    """Test get_coverage_dictionary when the left boundary is out of range."""
    tests_path = Path(os.path.abspath(__file__)).parent.parent
    test_spice_data_dir = tests_path / "test-data" / "test_spice_files"
    with spiceypy.KernelPool(
        [
            str(test_spice_data_dir / "naif0012.tls"),
            str(test_spice_data_dir / "imap_sclk_0012.tsc"),
        ]
    ):
        mock_wncard.return_value = 1
        mock_wnfetd.return_value = (
            spiceypy.utc2et("2000-01-01T00:00:00"),
            spiceypy.utc2et("2025-12-26T00:00:00"),
        )
        results_j2000, _results_datetime, results_sclk = get_coverage_dictionary(
            Path("/foo/bar/earth_000101_251226_250929.bpc")
        )
        assert results_j2000[0][0] == MAXIMUM_J2000_INTERVAL[0][0]
        assert results_sclk[0][0] == MAXIMUM_SCLK_INTERVAL[0][0]


# NOTE: We patch clear_ephemeral_storage to prevent it from deleting test files
# that are checked into git and needed for other unit tests. In production,
# Lambda ephemeral storage (/tmp) is automatically cleaned up after execution.
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.clear_ephemeral_storage"
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_s3_spice_files(mock_download, mock_clear, session, events_client, s3_client):
    """Test s3 event.

    The following test mimics a leapsecond kernel being placed on the SDS,
    followed by a spacecraft clock kernel, and then an attitude file.

    The files are located in the "test_spice_files" directory.

    """
    temp_path = os.getenv("DATA_DIR")
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")

    # Insert leapsecond spice kernel
    lsk_test_path = os.path.join(test_spice_data_dir, "naif0012.tls")
    put_local_file_in_bucket(
        s3_client,
        "imap/spice/lsk/naif0012.tls",
        lsk_test_path,
    )
    _insert_test_file(
        session,
        "naif0012.tls",
        "imap/spice/lsk/naif0012.tls",
        [[0, 1000000000]],  # Dummy intervals for testing
    )

    # Insert spacecraft clock spice kernel
    sclk_test_path = os.path.join(test_spice_data_dir, "imap_sclk_0012.tsc")
    clock_kernel_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/sclk/imap_sclk_0012.tsc",
        sclk_test_path,
    )
    _insert_test_file(
        session,
        "imap_sclk_0012.tsc",
        "imap/spice/sclk/imap_sclk_0012.tsc",
        [[0, 1000000000]],  # Dummy intervals for testing
    )

    def download_side_effect(path):
        if path.endswith("naif0012.tls"):
            return lsk_test_path
        elif path.endswith("imap_sclk_0012.tsc"):
            return sclk_test_path
        elif path.endswith("imap_2025_118_2025_120_001.ah.bc"):
            return Path(
                os.path.join(test_spice_data_dir, "imap_2025_118_2025_120_001.ah.bc")
            )
        else:
            raise ValueError(f"Unexpected download path: {path}")

    mock_download.side_effect = download_side_effect
    spice_indexer.lambda_handler(clock_kernel_event, None)

    # Insert a new attitude kernel
    attitude_kernel_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/ck/imap_2025_118_2025_120_001.ah.bc",
        os.path.join(test_spice_data_dir, "imap_2025_118_2025_120_001.ah.bc"),
    )
    _insert_test_file(
        session,
        "imap_2025_118_2025_120_001.ah.bc",
        "imap/spice/ck/imap_2025_118_2025_120_001.ah.bc",
        [
            [799240876.0732585, 799240921.0732585],
            [799242436.0732583, 799244783.0732579],
        ],  # J2000 intervals for testing
    )
    spice_indexer.lambda_handler(attitude_kernel_event, None)

    # Verify that the database was populated appropriately
    # NOTE: This is also testing the spice_query_api, to help ensure compatibility
    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "attitude_history"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["kernel_type"] == "attitude_history"
    assert result[0]["version"] == 1
    assert len(result[0]["file_intervals_datetime"]) == 1  # 1 gap detected
    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "leapseconds"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["kernel_type"] == "leapseconds"
    assert result[0]["version"] == 12
    assert len(result[0]["file_intervals_datetime"]) == 1  # Default time range

    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "spacecraft_clock"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["kernel_type"] == "spacecraft_clock"
    assert result[0]["version"] == 12
    assert len(result[0]["file_intervals_datetime"]) == 1  # Default time range

    # Checking metakernel API here as well!
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 0,
                "end_time": 1000000000,
                "spice_path": temp_path + "/imap/spice/",
            }
        },
        None,
    )


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.wnfetd"
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.wncard",
    return_value=1,
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.ckcov"
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.spiceypy.ckobj",
    return_value=[-43101],
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.clear_ephemeral_storage"
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_s3_lo_pivot_attitude_file(
    mock_download,
    mock_clear,
    mock_ckobj,
    mock_ckcov,
    mock_wncard,
    mock_wnfetd,
    session,
    events_client,
    s3_client,
    tmp_path,
):
    """Index a Lo pivot attitude kernel and check its coverage is the pointing."""
    tests_path = Path(os.path.abspath(__file__)).parent.parent
    test_spice_data_dir = tests_path / "test-data" / "test_spice_files"
    lsk_test_path = test_spice_data_dir / "naif0012.tls"
    sclk_test_path = test_spice_data_dir / "imap_sclk_0012.tsc"
    put_local_file_in_bucket(s3_client, "imap/spice/lsk/naif0012.tls", lsk_test_path)
    put_local_file_in_bucket(
        s3_client, "imap/spice/sclk/imap_sclk_0012.tsc", sclk_test_path
    )
    _insert_test_file(
        session, "naif0012.tls", "imap/spice/lsk/naif0012.tls", [[0, 1000000000]]
    )
    _insert_test_file(
        session,
        "imap_sclk_0012.tsc",
        "imap/spice/sclk/imap_sclk_0012.tsc",
        [[0, 1000000000]],
    )

    # The CK coverage calls are mocked to report a single pointing window,
    # so the kernel file contents are never read.
    with spiceypy.KernelPool([str(lsk_test_path)]):
        et_start = spiceypy.str2et("2025-10-11T01:00:00")
        et_end = spiceypy.str2et("2025-10-11T23:00:00")
    mock_wnfetd.return_value = (et_start, et_end)
    filename = "imap_lopivot-repoint00042_2025_284_2025_284_001.bc"
    lo_pivot_path = tmp_path / filename
    lo_pivot_path.write_bytes(b"")

    s3_key = str(
        SPICEFilePath(filename)
        .construct_path()
        .relative_to(imap_data_access.config["DATA_DIR"])
    )
    assert s3_key == f"imap/spice/ck/{filename}"

    def download_side_effect(path):
        if path.endswith("naif0012.tls"):
            return lsk_test_path
        elif path.endswith("imap_sclk_0012.tsc"):
            return sclk_test_path
        elif path.endswith(filename):
            return lo_pivot_path
        raise ValueError(f"Unexpected download path: {path}")

    mock_download.side_effect = download_side_effect
    event = put_local_file_in_bucket(s3_client, s3_key, lo_pivot_path)
    spice_indexer.lambda_handler(event, None)
    assert mock_ckcov.call_args.kwargs["idcode"] == -43101

    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "lo_pivot_attitude"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["file_name"] == f"ck/{filename}"
    assert result[0]["kernel_type"] == "lo_pivot_attitude"
    assert result[0]["version"] == 1
    # Coverage should be the pointing window, not the whole mission
    assert result[0]["min_date_j2000"] == et_start
    assert result[0]["max_date_j2000"] == et_end
    assert result[0]["file_intervals_j2000"] == [[et_start, et_end]]


def test_get_spin_file_coverage():
    """Spin coverage runs from the first spin start to the end of the last spin."""
    filename = "imap_2026_267_2026_267_01.spin.csv"
    start, end = get_spin_file_coverage(TEST_SPICE_DATA_DIR / filename)
    expected_start, expected_end = SPIN_COVERAGE[filename]
    assert start == expected_start.replace(tzinfo=timezone.utc)
    assert end == expected_end.replace(tzinfo=timezone.utc)


def test_get_spin_file_coverage_skips_comments(tmp_path):
    """Comment lines are skipped, as imap_processing does when reading spin files."""
    spin_file = tmp_path / "imap_2026_267_2026_267_01.spin.csv"
    spin_file.write_text(
        "# A comment line\n"
        "spin_number,spin_start_utc,spin_period_sec\n"
        "1,2026-09-24 14:01:41.000000,15\n"
        "# Another comment line\n"
        "2,2026-09-24 14:01:56.000000,15.5\n"
        # A truncated row with no spin start time is skipped
        "3\n"
    )
    start, end = get_spin_file_coverage(spin_file)
    assert start == datetime(2026, 9, 24, 14, 1, 41, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 24, 14, 2, 11, 500000, tzinfo=timezone.utc)


def test_get_spin_file_coverage_no_rows(tmp_path):
    """A spin file with only a header has no coverage."""
    spin_file = tmp_path / "imap_2026_267_2026_267_01.spin.csv"
    spin_file.write_text("spin_number,spin_start_utc,spin_period_sec\n")
    with pytest.raises(ValueError, match="No spin rows found"):
        get_spin_file_coverage(spin_file)


@pytest.mark.parametrize("filename", SFF_COVERAGE)
def test_get_small_forces_file_coverage(filename):
    """SFF coverage runs from the first record's start to the last record's end."""
    start, end = get_small_forces_file_coverage(TEST_SPICE_DATA_DIR / filename)
    expected_start, expected_end = SFF_COVERAGE[filename]
    assert start == expected_start.replace(tzinfo=timezone.utc)
    assert end == expected_end.replace(tzinfo=timezone.utc)


SFF_HEADER = "FILE_TYPE,SFF\nSTARTTIME/ENDTIME_TIMEZONE,UTC\n"
SFF_COLUMNS = "INDEX,RecType,INBURN,STARTTIME,ENDTIME,DTIME\n"
SFF_RECORD = (
    "1, H, 0, 2026 SEP 30 13:02:06.662376, 2026 SEP 30 13:02:07.662375,    1.000\n"
)


@pytest.mark.parametrize(
    ("contents", "error"),
    [
        (SFF_HEADER + SFF_COLUMNS + SFF_RECORD, r"No \$\$EOH line found"),
        (SFF_HEADER + "$$EOH\n" + SFF_RECORD, "No record column names found"),
        (
            SFF_HEADER.replace(",UTC", ",ET") + SFF_COLUMNS + "$$EOH\n" + SFF_RECORD,
            "Expected UTC record times",
        ),
        (SFF_HEADER + SFF_COLUMNS + "$$EOH\n\n", "No records found"),
    ],
)
def test_get_small_forces_file_coverage_invalid(tmp_path, contents, error):
    """Small forces files that don't match the expected layout raise an error."""
    sff_file = tmp_path / "imap_2026_273_2026_273_hist_01.sff"
    sff_file.write_text(contents)
    with pytest.raises(ValueError, match=error):
        get_small_forces_file_coverage(sff_file)


def test_s3_spin_files(session, s3_client, events_client, time_kernels):
    """Test s3 event.

    The following test mimics a spin file being placed on the SDC. Each spin
    file is indexed into the SPICE table with the coverage from its rows, and
    into the spin table with the dates from its filename.
    """
    # First spin file ingestion
    spin_file1_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv",
        TEST_SPICE_DATA_DIR / "imap_2026_267_2026_267_01.spin.csv",
    )
    spice_indexer.lambda_handler(spin_file1_event, None)
    query = select(models.SpinFiles.__table__)
    spin_table_rows = session.execute(query).all()
    assert len(spin_table_rows) == 1
    assert spin_table_rows[0].file_path == (
        "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv"
    )
    assert spin_table_rows[0].start_date == datetime(2026, 9, 24)
    assert spin_table_rows[0].end_date == datetime(2026, 9, 24)

    # Second spin file ingestion
    spin_file2_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/spin/imap_2026_267_2026_267_02.spin.csv",
        TEST_SPICE_DATA_DIR / "imap_2026_267_2026_267_02.spin.csv",
    )
    spice_indexer.lambda_handler(spin_file2_event, None)
    query = select(models.SpinFiles.__table__)
    spin_table_rows = session.execute(query).all()
    assert len(spin_table_rows) == 2
    assert spin_table_rows[1].file_path == (
        "imap/spice/spin/imap_2026_267_2026_267_02.spin.csv"
    )
    assert spin_table_rows[1].version == "02"

    spice_rows = _spice_table_rows(session, "spin")
    assert [row.file_name for row in spice_rows] == [
        "imap_2026_267_2026_267_01.spin.csv",
        "imap_2026_267_2026_267_02.spin.csv",
    ]
    assert [row.version for row in spice_rows] == [1, 2]
    # Both versions share a file root, so the "latest" query keeps only _02
    assert {row.file_root for row in spice_rows} == {"imap_2026_267_2026_267_.spin.csv"}
    _assert_coverage(
        spice_rows[0], *SPIN_COVERAGE["imap_2026_267_2026_267_01.spin.csv"]
    )
    start, end = get_spin_file_coverage(
        TEST_SPICE_DATA_DIR / "imap_2026_267_2026_267_02.spin.csv"
    )
    _assert_coverage(
        spice_rows[1], start.replace(tzinfo=None), end.replace(tzinfo=None)
    )

    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "spin", "latest": "true"}}, None
    )
    result = json.loads(result["body"])
    assert [row["file_name"] for row in result] == [
        "spin/imap_2026_267_2026_267_02.spin.csv"
    ]


def test_s3_small_forces_files_spice_table(
    session, s3_client, events_client, time_kernels
):
    """Small forces files are indexed into the SPICE table and their own table."""
    filenames = [
        "imap_2026_273_2026_273_hist_01.sff",
        "imap_2026_273_2026_273_hist_02.sff",
        "imap_2026_273_2026_274_hist_01.sff",
    ]
    for filename in filenames:
        event = put_local_file_in_bucket(
            s3_client,
            f"imap/spice/activities/{filename}",
            TEST_SPICE_DATA_DIR / filename,
        )
        spice_indexer.lambda_handler(event, None)

    small_forces_rows = session.execute(select(models.SmallForcesFile.__table__)).all()
    assert len(small_forces_rows) == 3
    # The small forces table keeps the dates from the filename
    assert small_forces_rows[2].start_date == datetime(2026, 9, 30)
    assert small_forces_rows[2].end_date == datetime(2026, 10, 1)

    spice_rows = _spice_table_rows(session, "thruster")
    assert [row.file_name for row in spice_rows] == filenames
    for row in spice_rows:
        _assert_coverage(row, *SFF_COVERAGE[row.file_name])
    # Both versions of the 273_273 file share a file root
    assert spice_rows[0].file_root == spice_rows[1].file_root
    assert [row.version for row in spice_rows] == [1, 2, 1]
    # The latest 273_273 file ends exactly where the 273_274 file starts
    assert spice_rows[1].max_date_j2000 == spice_rows[2].min_date_j2000


def test_index_spice_file_spin_upsert(session, s3_client, time_kernels):
    """Re-indexing a spin file updates its SPICE table row instead of failing."""
    s3_path = "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv"
    put_local_file_in_bucket(
        s3_client, s3_path, TEST_SPICE_DATA_DIR / "imap_2026_267_2026_267_01.spin.csv"
    )
    index_spice_file(s3_path)
    index_spice_file(s3_path)

    spice_rows = _spice_table_rows(session, "spin")
    assert len(spice_rows) == 1
    _assert_coverage(
        spice_rows[0], *SPIN_COVERAGE["imap_2026_267_2026_267_01.spin.csv"]
    )


def test_s3_spin_file_spice_table_failure(session, s3_client, events_client):
    """The spin table is still indexed when indexing into the SPICE table fails.

    No leapseconds or spacecraft clock kernels are available, so the SPICE table
    indexing fails.
    """
    event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv",
        TEST_SPICE_DATA_DIR / "imap_2026_267_2026_267_01.spin.csv",
    )
    spice_indexer.lambda_handler(event, None)

    assert len(session.execute(select(models.SpinFiles.__table__)).all()) == 1
    assert _spice_table_rows(session, "spin") == []


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.get_file_ingestion_date",
    return_value=datetime(2025, 1, 1, 10, 0, 0),
)
def test_s3_small_forces_files(mock_get_ingestion_date, session):
    """Test indexing small-forces files."""
    # Index first small_forces file
    s3_key_1 = "imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff"
    index_small_forces_file(s3_key_1)

    query = select(models.SmallForcesFile.__table__)
    small_forces_table_rows = session.execute(query).all()
    assert len(small_forces_table_rows) == 1
    assert small_forces_table_rows[0].file_path == s3_key_1
    assert small_forces_table_rows[0].version == "01"

    # Index second small_forces file
    s3_key_2 = "imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff"
    index_small_forces_file(s3_key_2)

    query = select(models.SmallForcesFile.__table__)
    small_forces_table_rows = session.execute(query).all()
    assert len(small_forces_table_rows) == 2
    assert small_forces_table_rows[1].file_path == s3_key_2
    assert small_forces_table_rows[1].version == "02"


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_s3_repoint_files(mock_download, session, s3_client, events_client):
    """Test s3 event for repoint files."""
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")
    # Mock download to return the test file path
    repoint_file = os.path.join(test_spice_data_dir, "imap_2000_056_03.repoint.csv")
    mock_download.return_value = repoint_file
    # Repoint file ingestion test
    repoint_file_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/repoint/imap_2000_056_03.repoint.csv",
        os.path.join(test_spice_data_dir, "imap_2000_056_03.repoint.csv"),
    )
    spice_indexer.lambda_handler(repoint_file_event, None)
    # Query PointingTable to verify ingestion
    pointing_ids = session.query(models.PointingTable.pointing_id).all()
    assert len(pointing_ids) == 49

    # Query RepointFiles table to verify it was populated
    repoint_file_entry = (
        session.query(models.RepointFiles)
        .filter_by(file_path="imap/spice/repoint/imap_2000_056_03.repoint.csv")
        .first()
    )
    assert repoint_file_entry is not None
    assert repoint_file_entry.version == "03"
    assert (
        repoint_file_entry.file_path
        == "imap/spice/repoint/imap_2000_056_03.repoint.csv"
    )
    # The end_date should be from the last pointing entry
    last_pointing = (
        session.query(models.PointingTable)
        .order_by(models.PointingTable.pointing_id.desc())
        .first()
    )
    expected_end_date = (
        last_pointing.pointing_end_utc or last_pointing.pointing_start_utc
    )
    assert repoint_file_entry.end_date == expected_end_date
    assert repoint_file_entry.ingestion_date is not None


def test_send_spice_event(session, events_client, s3_client):
    """Test the ``send_spice_event`` function."""
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")

    s3_key = "imap/spice/lsk/naif0012.tls"
    put_local_file_in_bucket(
        s3_client, s3_key, os.path.join(test_spice_data_dir, "naif0012.tls")
    )
    _insert_test_file(
        session,
        "naif0012.tls",
        s3_key,
        [[0, 1000000000]],  # Dummy intervals for testing
    )
    spice_obj = SPICEFilePath(s3_key)
    result = spice_indexer.send_spice_event(spice_obj, s3_key)
    assert result is None

    # Now pass attitude kernel
    s3_key = "imap/spice/ck/imap_2025_118_2025_120_001.ah.bc"
    put_local_file_in_bucket(
        s3_client,
        s3_key,
        os.path.join(test_spice_data_dir, "imap_2025_118_2025_120_001.ah.bc"),
    )
    _insert_test_file(
        session,
        "imap_2025_118_2025_120_001.ah.bc",
        s3_key,
        [
            [799240876.0732585, 799240921.0732585],
            [799242436.0732583, 799244783.0732579],
        ],  # J2000 intervals for testing
    )
    spice_obj = SPICEFilePath(s3_key)
    result = spice_indexer.send_spice_event(spice_obj, s3_key)
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200

    # Test that download fails if file doesn't exist
    s3_key = "imap/spice/ck/imap_2027_118_2027_120_001.ah.bc"
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": datetime.now().isoformat(),
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (s3_key),
                "reason": "PutObject",
            },
        },
    }
    with pytest.raises(ValueError, match="Error downloading file"):
        spice_indexer.lambda_handler(event, None)


def test_send_spice_event_filters_kernel_types(events_client):
    """Only reconstructed/current coverage kernels should emit events."""
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.boto3.client",
        return_value=events_client,
    ):
        for kernel_type in (
            "attitude_history",
            "pointing_attitude",
            "ephemeris_reconstructed",
            "attitude_predict",
            "ephemeris_predict",
        ):
            result = spice_indexer.send_spice_event(
                Mock(spice_metadata={"type": kernel_type}),
                "imap/spice/test/file",
            )
            assert result["ResponseMetadata"]["HTTPStatusCode"] == 200


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_index_pointing_data_updates_null_values(mock_download, session, tmpdir):
    """Test that Null values in the pointing table are updated."""
    new_repoint_file = os.path.join(tmpdir, "test_repoint.csv")
    mock_download.return_value = new_repoint_file
    # Create a test CSV file with repoint data
    test_csv_content = """repoint_id,repoint_start_utc,repoint_end_utc
    1,2025-07-01T10:00:00.000000,2025-07-01T10:10:00.000000
    2,2025-07-02T10:00:00.000000,2025-07-02T10:10:00.000000
    3,NaN,NaN
    """
    with open(new_repoint_file, "w") as f:
        f.write(test_csv_content)

    # Add an initial entry to the pointing table with Null values
    session.add(
        models.PointingTable(
            pointing_id=1,
            pointing_start_utc=datetime(2025, 7, 1, 10, 10, 0),
            pointing_end_utc=None,
            repoint_start_utc=None,
            repoint_end_utc=None,
        )
    )
    session.commit()

    # Call the function to index pointing data
    index_pointing_data("s3://test-bucket/test_repoint.csv")

    # Query the pointing table to verify updates
    first_pointing_entry = (
        session.query(models.PointingTable).filter_by(pointing_id=1).first()
    )

    # i_pointing repoint_end_utc
    assert first_pointing_entry.pointing_start_utc == datetime(2025, 7, 1, 10, 10, 0)
    # i_pointing + 1 repoint_end_utc
    assert first_pointing_entry.pointing_end_utc == datetime(2025, 7, 2, 10, 10, 0)
    # # i_pointing + 1 repoint_start_utc
    assert first_pointing_entry.repoint_start_utc == datetime(2025, 7, 2, 10, 0, 0)
    # # i_pointing + 1 repoint_end_utc
    assert first_pointing_entry.repoint_end_utc == datetime(2025, 7, 2, 10, 10, 0)


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.get_file_ingestion_date",
    return_value=datetime(2025, 1, 1, 10, 0, 0),
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_index_repoint_file_with_null_pointing_end_utc(
    mock_download, mock_get_ingestion_date, session, tmp_path
):
    """Test index_repoint_file when repoint_end_utc is a NaN."""
    new_repoint_file = tmp_path / "imap_2025_100_01.repoint.csv"
    mock_download.return_value = new_repoint_file

    # Create a test CSV file with repoint data
    test_csv_content = """repoint_id,repoint_start_utc,repoint_end_utc
1,2025-04-10T10:00:00.000000,2025-04-10T10:10:00.000000
2,2025-04-11T10:00:00.000000,NaN
"""
    with open(new_repoint_file, "w") as f:
        f.write(test_csv_content)

    # First index the pointing data
    index_pointing_data("imap/spice/repoint/imap_2025_100_01.repoint.csv")

    # Get the last pointing entry and verify it has None for pointing_end_utc
    last_pointing = (
        session.query(models.PointingTable)
        .order_by(models.PointingTable.pointing_id.desc())
        .first()
    )
    assert last_pointing.pointing_start_utc is None

    # Now call index_repoint_file directly
    from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer import (
        index_repoint_file,
    )

    index_repoint_file("imap/spice/repoint/imap_2025_100_01.repoint.csv")

    # Verify the repoint file was indexed with the pointing_start_utc as end_date
    repoint_entry = (
        session.query(models.RepointFiles)
        .filter_by(file_path="imap/spice/repoint/imap_2025_100_01.repoint.csv")
        .first()
    )
    assert repoint_entry is not None
    assert repoint_entry.version == "01"
    # Since pointing_end_utc is None, it should use pointing_start_utc
    assert repoint_entry.end_date == parse_datetime("2025-04-11T10:00:00.000000")
    assert repoint_entry.ingestion_date is not None


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.get_file_ingestion_date",
    return_value=datetime(2025, 1, 1, 10, 0, 0),
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_index_repoint_file_multiple_versions(
    mock_download, mock_get_ingestion_date, session, tmpdir
):
    """Test indexing multiple repoint files with different versions."""
    # Create first repoint file (version 01)
    repoint_file_v01 = os.path.join(tmpdir, "imap_2025_200_01.repoint.csv")
    test_csv_v01 = """repoint_id,repoint_start_utc,repoint_end_utc
1,2025-07-19T10:00:00.000000,2025-07-19T10:10:00.000000
2,2025-07-20T10:00:00.000000,2025-07-20T10:10:00.000000
"""
    with open(repoint_file_v01, "w") as f:
        f.write(test_csv_v01)

    mock_download.return_value = repoint_file_v01

    # Index first version
    index_pointing_data("imap/spice/repoint/imap_2025_200_01.repoint.csv")
    from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer import (
        index_repoint_file,
    )

    index_repoint_file("imap/spice/repoint/imap_2025_200_01.repoint.csv")

    # Verify first version was indexed
    repoint_v01 = (
        session.query(models.RepointFiles)
        .filter_by(file_path="imap/spice/repoint/imap_2025_200_01.repoint.csv")
        .first()
    )
    assert repoint_v01 is not None
    assert repoint_v01.version == "01"

    # Create second repoint file (version 02) with more data
    repoint_file_v02 = os.path.join(tmpdir, "imap_2025_200_02.repoint.csv")
    test_csv_v02 = """repoint_id,repoint_start_utc,repoint_end_utc
1,2025-07-19T10:00:00.000000,2025-07-19T10:10:00.000000
2,2025-07-20T10:00:00.000000,2025-07-20T10:10:00.000000
3,2025-07-21T10:00:00.000000,2025-07-21T10:10:00.000000
"""
    with open(repoint_file_v02, "w") as f:
        f.write(test_csv_v02)

    mock_download.return_value = repoint_file_v02

    # Index second version
    index_pointing_data("imap/spice/repoint/imap_2025_200_02.repoint.csv")
    index_repoint_file("imap/spice/repoint/imap_2025_200_02.repoint.csv")

    # Verify second version was indexed
    repoint_v02 = (
        session.query(models.RepointFiles)
        .filter_by(file_path="imap/spice/repoint/imap_2025_200_02.repoint.csv")
        .first()
    )
    assert repoint_v02.version == "02"

    # Verify both versions exist in the database
    all_repoint_files = session.query(models.RepointFiles).all()
    assert len(all_repoint_files) == 2


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.get_file_ingestion_date",
    return_value=datetime(2025, 1, 1, 10, 0, 0),
)
def test_index_small_forces_file(mock_get_ingestion_date, session):
    """Test index_small_forces_file function."""
    # Call index_small_forces_file
    index_small_forces_file(
        "imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff"
    )

    # Verify the small-forces file was indexed
    small_forces_entry = (
        session.query(models.SmallForcesFile)
        .filter_by(
            file_path="imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff"
        )
        .first()
    )
    assert small_forces_entry is not None
    assert small_forces_entry.version == "01"
    assert small_forces_entry.start_date == datetime(2025, 4, 10, 0, 0, 0)
    assert small_forces_entry.end_date == datetime(2025, 4, 20, 0, 0, 0)
    assert small_forces_entry.ingestion_date is not None


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.get_file_ingestion_date",
    return_value=datetime(2025, 1, 1, 10, 0, 0),
)
def test_index_small_forces_file_multiple_versions(mock_get_ingestion_date, session):
    """Test indexing multiple small-forces files with different versions."""
    # Index first version
    index_small_forces_file(
        "imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff"
    )

    # Verify first version was indexed
    small_forces_v01 = (
        session.query(models.SmallForcesFile)
        .filter_by(
            file_path="imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff"
        )
        .first()
    )
    assert small_forces_v01 is not None
    assert small_forces_v01.version == "01"

    # Index second version
    index_small_forces_file(
        "imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff"
    )

    # Verify second version was indexed
    small_forces_v02 = (
        session.query(models.SmallForcesFile)
        .filter_by(
            file_path="imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff"
        )
        .first()
    )
    assert small_forces_v02 is not None
    assert small_forces_v02.version == "02"

    # Verify both versions exist in the database
    all_small_forces_files = session.query(models.SmallForcesFile).all()
    assert len(all_small_forces_files) == 2


def test_clear_ephemeral_storage(tmp_path):
    """Test that clear_ephemeral_storage deletes temporary files."""
    # Create a temporary file
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("test content")
    assert test_file.exists()

    # Clear the file
    clear_ephemeral_storage(test_file)

    # Verify file was deleted
    assert not test_file.exists()


def test_clear_ephemeral_storage_nonexistent_file(tmp_path):
    """Test that clear_ephemeral_storage handles nonexistent files gracefully."""
    # Try to clear a file that doesn't exist
    nonexistent_file = tmp_path / "nonexistent.txt"
    assert not nonexistent_file.exists()

    # Should not raise an exception
    clear_ephemeral_storage(nonexistent_file)
