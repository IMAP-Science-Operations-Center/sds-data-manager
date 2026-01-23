"""Tests for the SPICE indexer lambda."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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
    get_coverage_dictionary,
    index_pointing_data,
    parse_datetime,
    index_small_forces_file,
)


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


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer.download_from_s3"
)
def test_s3_spice_files(mock_download, session, events_client, s3_client):
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


def test_s3_spin_files(session, s3_client, events_client):
    """Test s3 event.

    The following test mimics a spin file being placed on the SDC.
    The files are located in the "test_spice_files" directory.

    """
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")

    # First spin file ingestion
    spin_file1_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv",
        os.path.join(test_spice_data_dir, "imap_2026_267_2026_267_01.spin.csv"),
    )
    spice_indexer.lambda_handler(spin_file1_event, None)
    query = select(models.SpinFiles.__table__)
    spin_table_rows = session.execute(query).all()
    assert len(spin_table_rows) == 1
    assert spin_table_rows[0].file_path == (
        "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv"
    )

    # Second spin file ingestion
    spin_file2_event = put_local_file_in_bucket(
        s3_client,
        "imap/spice/spin/imap_2026_267_2026_267_02.spin.csv",
        os.path.join(test_spice_data_dir, "imap_2026_267_2026_267_02.spin.csv"),
    )
    spice_indexer.lambda_handler(spin_file2_event, None)
    query = select(models.SpinFiles.__table__)
    spin_table_rows = session.execute(query).all()
    assert len(spin_table_rows) == 2
    assert spin_table_rows[1].file_path == (
        "imap/spice/spin/imap_2026_267_2026_267_02.spin.csv"
    )
    assert spin_table_rows[1].version == "02"


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
    index_small_forces_file("imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff")

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
    index_small_forces_file("imap/spice/small-forces/imap_2025_100_2025_110_hist_01.sff")

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
    index_small_forces_file("imap/spice/small-forces/imap_2025_100_2025_110_hist_02.sff")

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
