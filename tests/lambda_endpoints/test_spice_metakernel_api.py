"""Tests for the SPICE Query API."""

import json
import os
from datetime import datetime, timedelta

import imap_data_access
import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_metakernel_api
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.spice_utilities import (
    MAXIMUM_MISSION_J2000_TIME,
)


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


def _insert_test_file(session, filename, intervals, upload_time=0):
    spice_object = imap_data_access.SPICEFilePath(filename)
    version = spice_object.spice_metadata["version"]
    metadata_params = {
        "file_name": filename,
        "file_path": f"imap/spice/{filename}",
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


def _insert_test_data(session):
    """Put a filepath into the test data.

    The comments for each inserted file below assume
    that a user is querying for data between t=1 to t=100
    """
    # This file should NOT be loaded, because there is a
    # a newer version of the file
    _insert_test_file(
        session, "imap_1000_001_1000_100_001.ah.bc", [[1, 50], [55, 65], [75, 100]]
    )
    # This file should be loaded, because it is a high
    # priority file covering a large time range
    _insert_test_file(
        session, "imap_1000_001_1000_100_002.ah.bc", [[1, 50], [55, 65], [75, 100]]
    )
    # This file should NOT be loaded in, it was uploaded too early
    _insert_test_file(session, "imap_1000_001_1000_055_002.ap.bc", [[1, 55]])

    # This file should NOT be loaded, because there is a
    # history file covering all of this data
    _insert_test_file(session, "imap_1000_010_1000_020_002.ap.bc", [[10, 20]])

    # This file should NOT be loaded, because there is a
    # history file covering all of this data
    _insert_test_file(session, "imap_1000_090_1000_100_002.ap.bc", [[90, 100]])

    # This file should be loaded in, because it was uploaded
    # more recently, so it has a higher priority.
    _insert_test_file(
        session, "imap_1000_060_1000_070_003.ap.bc", [[60, 70]], upload_time=10
    )

    # This file should be loaded, but only after the one directly above.
    # This contains data between 70-75 that needs filling in, but 65-70
    # will be done by imap_1000_060_1000_070_003.ap.bc.
    _insert_test_file(
        session, "imap_1000_065_1000_090_003.ap.bc", [[65, 90]], upload_time=2
    )

    # This file should NOT be loaded, because the file just
    # before this one has a higher version number. Even
    # though this file was uploaded at a later date, version
    # always takes precidence.
    _insert_test_file(
        session, "imap_1000_065_1000_090_001.ap.bc", [[65, 90]], upload_time=100
    )

    # This file should be loaded, because there has been no
    # data for 50-55 so far.
    _insert_test_file(
        session, "imap_1000_001_1000_300_003.ap.bc", [[1, 300]], upload_time=1
    )


def test_metakernel(session):
    """Tests that metakernel works as predicted."""
    _insert_test_data(session)
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "end_time": 100,
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )

    """
    This SPICE metakernel should have found the following files:

    1) imap_1000_001_1000_100_002.ah.bc - the best file to load in, because
        it is a history file with a large amount of coverage in the interval

    There are now gaps between 50-55 and 65-75

    2) imap_1000_060_1000_070_003.ap.bc - The next best file to load in.
        It was uploaded recently, and covers 65-70.

    There are now gaps between 50-55 and 70-75

    3) imap_1000_065_1000_090_003.ap.bc - This file covers the 70-75 gap
        that remains.

    There are now gaps between 50-55

    4) imap_1000_001_1000_300_003.ap.bc - This file covers everything
        in the time range, but it was uploaded very early in the mission,
        so it gets chose to plug in the remaining gaps in the time range

    There are now no gaps remaining.
    """

    results = json.loads(result["body"])
    assert len(results) == 4
    assert results[0] == "imap_1000_001_1000_300_003.ap.bc"
    assert results[1] == "imap_1000_065_1000_090_003.ap.bc"
    assert results[2] == "imap_1000_060_1000_070_003.ap.bc"
    assert results[3] == "imap_1000_001_1000_100_002.ah.bc"

    """
    If someone focuses the metakernel on a more specific time range, it should go
    straight to the appropriate file.
    """
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 53,
                "end_time": 54,
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )

    results = json.loads(result["body"])
    assert len(results) == 1
    assert results[0] == "imap_1000_001_1000_300_003.ap.bc"

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 20,
                "end_time": 25,
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )

    results = json.loads(result["body"])
    assert len(results) == 1
    assert results[0] == "imap_1000_001_1000_100_002.ah.bc"

    """
    Query the gap that two spice files individually cover
    """
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 65,
                "end_time": 75,
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )

    results = json.loads(result["body"])
    assert len(results) == 2
    assert results[0] == "imap_1000_065_1000_090_003.ap.bc"
    assert results[1] == "imap_1000_060_1000_070_003.ap.bc"

    """
    Metakernel generation tests
    """
    response = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 65,
                "end_time": 75,
                "spice_path": "",
                "list_files": "False",
            }
        },
        None,
    )
    assert response["statusCode"] == 200
    assert "KERNELS_TO_LOAD" in response["body"]
    assert "imap_1000_065_1000_090_003.ap.bc" in response["body"]


def test_metakernel_gaps(session):
    """Ensure it fails if gaps are detected."""
    _insert_test_data(session)
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "end_time": 100,
                "spice_path": "",
                "list_files": "True",
                "require_coverage": "True",
            }
        },
        None,
    )
    assert result["statusCode"] == 422


def test_metakernel_filtered_file_types(session):
    """Ensure it returns only 1 type of file."""
    _insert_test_file(session, "naif0012.tls", [[1, 300]], upload_time=1)
    _insert_test_file(session, "imap_sclk_0012.tsc", [[1, 300]], upload_time=1)
    _insert_test_data(session)

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "end_time": 100,
                "spice_path": "",
                "list_files": "True",
                "file_types": "leapseconds,spacecraft_clock",
            }
        },
        None,
    )
    assert len(json.loads(result["body"])) == 2
    assert json.loads(result["body"])[0] == "naif0012.tls"

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "end_time": 100,
                "spice_path": "",
                "list_files": "True",
                "file_types": "ephemeris_reconstructed",
            }
        },
        None,
    )
    assert result["statusCode"] == 404
    assert result["body"] == "No files found."


def test_metakernel_string_input(session, s3_client):
    """Test that string input is allowed, and is converted to a datetime object."""
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")

    # Insert leapsecond spice kernel into a mock S3 bucket
    lsk_test_path = os.path.join(test_spice_data_dir, "naif0012.tls")
    bucket_name = os.getenv("S3_BUCKET")
    with open(lsk_test_path, "rb") as f:
        s3_client.put_object(
            Bucket=bucket_name,
            Key="imap/spice/lsk/naif0012.tls",
            Body=f,
        )

    _insert_test_file(session, "imap/spice/lsk/naif0012.tls", [[1, 300]], upload_time=1)
    _insert_test_file(session, "imap_sclk_0012.tsc", [[1, 300]], upload_time=1)
    _insert_test_data(session)

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": "19000101",
                "end_time": "20260101",
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )
    assert len(json.loads(result["body"])) == 6


def test_metakernel_frames(session):
    """Test that the frame kernels are returned."""
    _insert_test_file(session, "imap_science_0001.tf", [[1, 300]], upload_time=1)
    _insert_test_file(session, "imap_001.tf", [[1, 300]], upload_time=1)

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 53,
                "end_time": 60,
                "file_types": "imap_frames,science_frames",
                "list_files": "True",
            }
        },
        None,
    )
    assert len(json.loads(result["body"])) == 2
    # Fip file type order and make sure both tf files are returned
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 53,
                "end_time": 60,
                "file_types": "science_frames,imap_frames",
                "list_files": "True",
            }
        },
        None,
    )
    assert len(json.loads(result["body"])) == 2


def test_metakernel_with_human_readable_dates(session, s3_client):
    """Test metakernel with human readable dates as input."""
    # Upload time kernel to S3 so furnish_best_spice_file can find it
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")

    lsk_test_path = os.path.join(test_spice_data_dir, "naif0012.tls")
    bucket_name = os.getenv("S3_BUCKET")
    with open(lsk_test_path, "rb") as f:
        s3_client.put_object(
            Bucket=bucket_name,
            Key="imap/spice/lsk/naif0012.tls",
            Body=f,
        )

    # Insert time kernel into db so furnish_best_spice_file can find it.
    _insert_test_file(
        session, "naif0012.tls", [[1, MAXIMUM_MISSION_J2000_TIME]], upload_time=1
    )
    # Insert attitude file for testing file_types parameter
    _insert_test_file(
        session, "imap_1000_001_1000_100_002.ah.bc", [[1, 300]], upload_time=1
    )

    # Attempt to query with human-readable date format without specifying file_types
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": "20260101",
                "end_time": "20260401",
                "list_files": "True",
            }
        },
        None,
    )
    # Should find leapseconds kernel and return successfully
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == ["naif0012.tls"]

    # Now query with file_types specified - should succeed
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": "19000101",
                "end_time": "20260101",
                "spice_path": "",
                "list_files": "True",
                "file_types": "attitude_history",
            }
        },
        None,
    )
    # Should return successfully with specific kernel available
    assert result["statusCode"] == 200
    # Should return the attitude file that was inserted
    assert json.loads(result["body"]) == ["imap_1000_001_1000_100_002.ah.bc"]


def test_convert_input_times_to_j2000_accepts_fractional_second_strings():
    """Fractional-second string times (as real query-string callers send) must parse.

    Regression test: the non-%Y%m%d fallback previously used int(), which
    raises an uncaught ValueError for any string containing a decimal point
    (e.g. "802027200.184") instead of parsing it, crashing the lambda.
    """
    start_time, end_time = spice_metakernel_api._convert_input_times_to_j2000(
        "802027200.184", "802027210.5"
    )
    assert start_time == pytest.approx(802027200.184)
    assert end_time == pytest.approx(802027210.5)


def test_metakernel_with_fractional_second_string_times(session):
    """The metakernel API must not crash on fractional-second string query times."""
    _insert_test_data(session)

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": "1.5",
                "end_time": "100.25",
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )

    assert result["statusCode"] == 200
    assert len(json.loads(result["body"])) == 4


def test_metakernel_no_query_string_parameters(session):
    """QueryStringParameters being None must not crash, and should include all data."""
    _insert_test_file(
        session,
        "imap_1000_001_1000_100_002.ah.bc",
        [[1, MAXIMUM_MISSION_J2000_TIME]],
    )

    result = spice_metakernel_api.lambda_handler(
        {"queryStringParameters": None},
        None,
    )

    assert result["statusCode"] == 200
    assert "KERNELS_TO_LOAD" in result["body"]
    assert "imap_1000_001_1000_100_002.ah.bc" in result["body"]


def test_metakernel_missing_start_and_end_time(session):
    """start_time/end_time omitted (other params present) must not crash."""
    _insert_test_file(
        session,
        "imap_1000_001_1000_100_002.ah.bc",
        [[1, MAXIMUM_MISSION_J2000_TIME]],
    )

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "spice_path": "",
                "list_files": "True",
            }
        },
        None,
    )

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == ["imap_1000_001_1000_100_002.ah.bc"]


def test_metakernel_only_start_time_provided(session):
    """When end_time is omitted, files should not be filtered by an upper bound."""
    # This file's coverage starts well after start_time and extends to the
    # end of the mission - it should still be included since no end_time
    # filter is applied.
    _insert_test_file(
        session,
        "imap_1000_001_1000_100_002.ah.bc",
        [[500000, MAXIMUM_MISSION_J2000_TIME]],
    )

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "list_files": "True",
            }
        },
        None,
    )

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == ["imap_1000_001_1000_100_002.ah.bc"]


def test_metakernel_only_end_time_provided(session):
    """When start_time is omitted, files should not be filtered by a lower bound."""
    # This file's coverage is entirely near the very start of the mission -
    # it should still be included since no start_time filter is applied.
    _insert_test_file(
        session,
        "imap_1000_001_1000_100_002.ah.bc",
        [[1, 50]],
    )

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "end_time": MAXIMUM_MISSION_J2000_TIME,
                "list_files": "True",
            }
        },
        None,
    )

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == ["imap_1000_001_1000_100_002.ah.bc"]
