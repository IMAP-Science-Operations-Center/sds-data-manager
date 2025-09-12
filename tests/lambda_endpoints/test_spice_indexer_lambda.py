"""Tests for the SPICE indexer lambda."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from imap_data_access import SPICEFilePath
from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.api_lambdas import (
    spice_metakernel_api,
    spice_query_api,
)
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.spice_indexer import (
    index_pointing_data,
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
