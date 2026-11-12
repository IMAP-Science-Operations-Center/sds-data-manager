"""Tests for the SPICE Query API."""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import imap_data_access
import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import (
    spice_metakernel_api,
    spice_query_api,
)
from sds_data_manager.lambda_code.SDSCode.api_lambdas.metakernel import MetaKernel
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.spice_utilities import (
    MAXIMUM_MISSION_J2000_TIME,
    metakernel_builder,
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


def test_metakernel_lo_pivot_attitude(session):
    """Lo pivot kernels: newest version per pointing, every pointing in range."""
    # Pointing 1 has two versions covering the same window; only v002 is used.
    _insert_test_file(
        session, "imap_lopivot-repoint00001_1000_001_1000_001_001.bc", [[1, 10]]
    )
    _insert_test_file(
        session,
        "imap_lopivot-repoint00001_1000_001_1000_001_002.bc",
        [[1, 10]],
        upload_time=-1,
    )
    # Pointings 2 and 3 do not overlap with each other or with pointing 1.
    _insert_test_file(
        session, "imap_lopivot-repoint00002_1000_001_1000_001_001.bc", [[20, 30]]
    )
    _insert_test_file(
        session, "imap_lopivot-repoint00003_1000_001_1000_001_001.bc", [[40, 50]]
    )
    # Pointing 4 is outside the requested range.
    _insert_test_file(
        session, "imap_lopivot-repoint00004_1000_001_1000_001_001.bc", [[200, 210]]
    )
    # Other kernel types are excluded by the file_types filter.
    _insert_test_data(session)

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "end_time": 100,
                "spice_path": "",
                "list_files": "True",
                "file_types": "lo_pivot_attitude",
            }
        },
        None,
    )
    assert result["statusCode"] == 200
    assert sorted(json.loads(result["body"])) == [
        "imap_lopivot-repoint00001_1000_001_1000_001_002.bc",
        "imap_lopivot-repoint00002_1000_001_1000_001_001.bc",
        "imap_lopivot-repoint00003_1000_001_1000_001_001.bc",
    ]


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


def test_metakernel_start_time_omitted_not_forwarded(session):
    """Omitted start_time must not appear as a key in the downstream query."""
    _insert_test_file(
        session,
        "imap_1000_001_1000_100_002.ah.bc",
        [[500000, MAXIMUM_MISSION_J2000_TIME]],
    )

    with patch(
        "sds_data_manager.lambda_code.SDSCode.spice_utilities.spice_query_api.lambda_handler",
        wraps=spice_query_api.lambda_handler,
    ) as spy:
        metakernel_builder(start_time=None, end_time=100)

    assert spy.call_count > 0
    for call in spy.call_args_list:
        query_string_parameters = call.args[0]["queryStringParameters"]
        assert "start_time" not in query_string_parameters
        assert query_string_parameters["end_time"] == 100


def test_metakernel_end_time_omitted_not_forwarded(session):
    """Omitted end_time must not appear as a key in the downstream query."""
    _insert_test_file(
        session,
        "imap_1000_001_1000_100_002.ah.bc",
        [[1, 50]],
    )

    with patch(
        "sds_data_manager.lambda_code.SDSCode.spice_utilities.spice_query_api.lambda_handler",
        wraps=spice_query_api.lambda_handler,
    ) as spy:
        metakernel_builder(start_time=1, end_time=None)

    assert spy.call_count > 0
    for call in spy.call_args_list:
        query_string_parameters = call.args[0]["queryStringParameters"]
        assert "end_time" not in query_string_parameters
        assert query_string_parameters["start_time"] == 1


@pytest.mark.parametrize(
    ("file_intervals", "expected_gaps"),
    [
        # No overlap at all: the whole gap remains.
        ([[0, 100]], [(300, 400)]),
        ([[500, 600]], [(300, 400)]),
        # Last segment ends before the gap (issue #1692): the result must not
        # reach back before gap_start or end before it starts.
        ([[0, 100], [110, 200], [210, 220]], [(300, 400)]),
        # Fully covered.
        ([[250, 450]], []),
        ([[250, 350], [350, 450]], []),
        # Partial coverage, clipped to the gap on both sides.
        ([[250, 320], [340, 360], [390, 450]], [(320, 340), (360, 390)]),
        ([[310, 320]], [(300, 310), (320, 400)]),
        # Unsorted input.
        ([[390, 450], [250, 320]], [(320, 390)]),
    ],
)
def test_calculate_gaps(file_intervals, expected_gaps):
    """Remaining gaps are the gap minus the file's intervals, within the gap."""
    assert MetaKernel._calculate_gaps(file_intervals, 300, 400) == expected_gaps


def _select(files, start=-10, end=400):
    """Run files (highest priority first) through the MetaKernel."""
    metakernel = MetaKernel(start, end, ["attitude"])
    for name, intervals in files:
        metakernel._check_file(
            {"file_name": name, "intervals": intervals}, "attitude", "intervals"
        )
    return metakernel


def test_growing_series_selects_only_newest_kernel():
    """Older kernels of a growing series add nothing and must not be selected.

    Regression test for #1692: with multi-segment kernels, older kernels in
    the series were selected and corrupted the remaining-gap list.
    """
    metakernel = _select(
        [
            ("278", [[0, 100], [110, 200], [210, 300]]),
            ("220", [[0, 100], [110, 200], [210, 220]]),
            ("206", [[0, 100], [110, 200], [210, 206.5]]),
        ]
    )

    assert [f["file_name"] for f in metakernel.spice_files["attitude"]] == ["278"]
    assert sorted(metakernel.spice_gaps["attitude"]) == [
        (-10, 0),
        (100, 110),
        (200, 210),
        (300, 400),
    ]


def test_older_kernel_filling_real_hole_is_selected():
    """An older kernel with data where the newest has a hole is still selected."""
    metakernel = _select(
        [
            ("278", [[0, 100], [110, 300]]),
            ("220", [[0, 220]]),
            ("206", [[0, 206]]),
        ]
    )

    assert [f["file_name"] for f in metakernel.spice_files["attitude"]] == [
        "278",
        "220",
    ]
    assert sorted(metakernel.spice_gaps["attitude"]) == [(-10, 0), (300, 400)]


def test_metakernel_growing_attitude_history_series(session):
    """The API returns only the newest kernel of a growing ah series."""
    _insert_test_file(
        session,
        "imap_2026_189_2026_206_002.ah.bc",
        [[0, 100], [110, 200], [210, 206.5]],
    )
    _insert_test_file(
        session,
        "imap_2026_189_2026_220_002.ah.bc",
        [[0, 100], [110, 200], [210, 220]],
        upload_time=1,
    )
    _insert_test_file(
        session,
        "imap_2026_189_2026_278_001.ah.bc",
        [[0, 100], [110, 200], [210, 300]],
        upload_time=2,
    )

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 0,
                "end_time": 300,
                "file_types": "attitude_history",
                "list_files": "True",
            }
        },
        None,
    )

    assert json.loads(result["body"]) == ["imap_2026_189_2026_278_001.ah.bc"]


def _list_files(file_types, start_time, end_time, require_coverage=False):
    """Call the metakernel API for a list of files."""
    query_params = {
        "start_time": start_time,
        "end_time": end_time,
        "file_types": file_types,
        "list_files": "True",
    }
    if require_coverage:
        query_params["require_coverage"] = "True"
    return spice_metakernel_api.lambda_handler(
        {"queryStringParameters": query_params}, None
    )


def test_metakernel_excludes_spin_and_thruster_by_default(session):
    """Spin and small forces files are only listed when requested."""
    _insert_test_file(session, "naif0012.tls", [[0, 300]])
    _insert_test_file(session, "imap_2026_267_2026_267_01.spin.csv", [[0, 300]])
    _insert_test_file(session, "imap_2026_273_2026_273_hist_01.sff", [[0, 300]])

    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 0,
                "end_time": 300,
                "list_files": "True",
            }
        },
        None,
    )
    assert json.loads(result["body"]) == ["naif0012.tls"]

    # Their categories aren't tracked, so they can't be reported as gaps
    metakernel = metakernel_builder(0, 300)
    assert "spin_category" not in metakernel.spice_gaps
    assert "thruster_category" not in metakernel.spice_gaps


def test_metakernel_tracks_only_requested_categories(session):
    """Categories that weren't requested aren't reported as gaps."""
    _insert_test_file(session, "imap_2026_267_2026_267_01.spin.csv", [[0, 300]])

    metakernel = metakernel_builder(0, 300, file_types={"SPIN"})
    assert list(metakernel.spice_gaps) == ["spin_category"]

    result = _list_files("spin", 0, 300, require_coverage=True)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == ["imap_2026_267_2026_267_01.spin.csv"]


@pytest.mark.parametrize(
    ("file_types", "filename"),
    [
        ("spin", "imap_2026_267_2026_267_01.spin.csv"),
        ("thruster", "imap_2026_273_2026_273_hist_01.sff"),
        ("leapseconds,thruster", "imap_2026_273_2026_273_hist_01.sff"),
    ],
)
def test_metakernel_file_rejects_spin_and_thruster(session, file_types, filename):
    """Spin and small forces files can only be listed, not put in a metakernel."""
    _insert_test_file(session, filename, [[0, 300]])
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 0,
                "end_time": 300,
                "file_types": file_types,
                "list_files": "False",
            }
        },
        None,
    )
    assert result["statusCode"] == 400
    assert "list_files=true" in result["body"]

    # The same request with list_files is allowed
    assert _list_files(file_types, 0, 300)["statusCode"] == 200


def test_metakernel_invalid_file_types(session):
    """Unknown file types are rejected instead of returning no files."""
    result = _list_files("spin,not_a_type", 0, 300)
    assert result["statusCode"] == 400
    assert "NOT_A_TYPE" in result["body"]


def test_metakernel_spin_order(session):
    """Spin files are listed lowest priority first, so the best file is last.

    The latest ingested file has the highest priority, whatever its version.
    Versions restart with each new file name, as in production, where
    imap_2025_337_2025_337_05 was followed by imap_2025_337_2025_338_03.
    """
    _insert_test_file(
        session, "imap_2025_337_2025_337_05.spin.csv", [[0, 100]], upload_time=0
    )
    # Ingested later, with a lower version
    _insert_test_file(
        session, "imap_2025_337_2025_338_03.spin.csv", [[50, 200]], upload_time=1
    )

    result = _list_files("spin", 0, 200, require_coverage=True)
    assert json.loads(result["body"]) == [
        "imap_2025_337_2025_337_05.spin.csv",
        "imap_2025_337_2025_338_03.spin.csv",
    ]


def test_metakernel_spin_long_file_spans_shorter_files(session):
    """A longer file spanning shorter ones covers the window without gaps.

    Mirrors 2026-07-24, where imap_2026_204_2026_206_02 sits next to single-day
    pieces of the same version.
    """
    _insert_test_file(session, "imap_2026_204_2026_206_02.spin.csv", [[0, 300]])
    _insert_test_file(
        session, "imap_2026_204_2026_204_02.spin.csv", [[0, 100]], upload_time=1
    )
    _insert_test_file(
        session, "imap_2026_205_2026_205_02.spin.csv", [[100, 200]], upload_time=1
    )

    result = _list_files("spin", 0, 300, require_coverage=True)
    assert result["statusCode"] == 200
    assert set(json.loads(result["body"])) == {
        "imap_2026_204_2026_206_02.spin.csv",
        "imap_2026_204_2026_204_02.spin.csv",
        "imap_2026_205_2026_205_02.spin.csv",
    }


@pytest.mark.parametrize(
    ("v10_intervals", "expected"),
    [
        # The _10 files cover all of the _01 file, so it is superseded
        (
            [[0, 150], [150, 300]],
            [
                "imap_2025_359_2025_360_10.spin.csv",
                "imap_2025_360_2025_361_10.spin.csv",
            ],
        ),
        # The _10 files end before the _01 file does, so it covers the rest
        (
            [[0, 150], [150, 250]],
            [
                "imap_2025_360_2025_360_01.spin.csv",
                "imap_2025_359_2025_360_10.spin.csv",
                "imap_2025_360_2025_361_10.spin.csv",
            ],
        ),
    ],
)
def test_metakernel_spin_higher_version_different_ranges(
    session, v10_intervals, expected
):
    """Higher-version files supersede a lower version only where they cover it.

    The #1708 and #1713 cases: imap_2025_360_2025_360_01 is replaced by _10 files
    that split the days differently.
    """
    _insert_test_file(session, "imap_2025_360_2025_360_01.spin.csv", [[100, 300]])
    _insert_test_file(
        session,
        "imap_2025_359_2025_360_10.spin.csv",
        [v10_intervals[0]],
        upload_time=1,
    )
    _insert_test_file(
        session,
        "imap_2025_360_2025_361_10.spin.csv",
        [v10_intervals[1]],
        upload_time=1,
    )

    result = _list_files("spin", 0, 300, require_coverage=True)
    assert result["statusCode"] == 200
    files = json.loads(result["body"])
    assert sorted(files) == sorted(expected)
    # The _01 file, if used, has the lowest priority, so it is listed first
    if "imap_2025_360_2025_360_01.spin.csv" in expected:
        assert files[0] == "imap_2025_360_2025_360_01.spin.csv"


@pytest.mark.parametrize(
    ("second_file_start", "status_code"),
    [
        (100.02, 200),  # 20 ms seam between files
        (99.98, 200),  # 20 ms overlap between files
        (115.0, 422),  # A missing spin, about 15 s
    ],
)
def test_metakernel_spin_seam_tolerance(session, second_file_start, status_code):
    """Seams between spin files are ignored, but a missing spin is a gap."""
    _insert_test_file(session, "imap_2026_267_2026_267_01.spin.csv", [[0, 100]])
    _insert_test_file(
        session,
        "imap_2026_268_2026_268_01.spin.csv",
        [[second_file_start, 200]],
        upload_time=1,
    )

    result = _list_files("spin", 0, 200, require_coverage=True)
    assert result["statusCode"] == status_code
    if status_code == 422:
        assert json.loads(result["body"]) == {"spin_category": [[100, 115.0]]}


def test_metakernel_spin_gap_tolerance_keeps_small_window(session):
    """A query window shorter than the tolerance is still a gap with no files."""
    result = _list_files("spin", 0, 0.5, require_coverage=True)
    assert result["statusCode"] == 422


# Two files of each list-only type with different names, the second ingested later
EDGE_GAP_FILES = {
    "spin": (
        "imap_2026_267_2026_267_01.spin.csv",
        "imap_2026_267_2026_268_01.spin.csv",
    ),
    "thruster": (
        "imap_2026_273_2026_273_hist_01.sff",
        "imap_2026_273_2026_274_hist_01.sff",
    ),
}


@pytest.mark.parametrize("kernel_type", EDGE_GAP_FILES)
@pytest.mark.parametrize(
    ("intervals", "expected_gap"),
    [
        ([[0.5, 200]], [0, 0.5]),  # Data starts after the window does
        ([[0, 199.5]], [199.5, 200]),  # Data ends before the window does
    ],
)
def test_metakernel_short_gap_at_window_edge(
    session, kernel_type, intervals, expected_gap
):
    """A gap at either end of the window is kept, however short.

    The seam tolerance only applies between files. No file is on the other side
    of a gap at the window's edge, so it is missing coverage.
    """
    _insert_test_file(session, EDGE_GAP_FILES[kernel_type][0], intervals)

    result = _list_files(kernel_type, 0, 200, require_coverage=True)
    assert result["statusCode"] == 422
    assert json.loads(result["body"]) == {f"{kernel_type}_category": [expected_gap]}


@pytest.mark.parametrize("kernel_type", EDGE_GAP_FILES)
def test_metakernel_lower_priority_file_fills_short_edge_gap(session, kernel_type):
    """A lower-priority file is used to fill a short gap at the window's edge."""
    older, newer = EDGE_GAP_FILES[kernel_type]
    _insert_test_file(session, older, [[0, 100]])
    _insert_test_file(session, newer, [[0.5, 200]], upload_time=1)

    result = _list_files(kernel_type, 0, 200, require_coverage=True)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == [older, newer]


def test_metakernel_thruster_versions_and_seam(session):
    """The latest small forces file is used, and consecutive files tile cleanly.

    Mirrors imap_2026_273_2026_273_hist_02, which starts 1 s after _01 and ends
    exactly where imap_2026_273_2026_274_hist_01 starts.
    """
    _insert_test_file(session, "imap_2026_273_2026_273_hist_01.sff", [[10, 100]])
    _insert_test_file(
        session, "imap_2026_273_2026_273_hist_02.sff", [[11, 100]], upload_time=1
    )
    _insert_test_file(
        session, "imap_2026_273_2026_274_hist_01.sff", [[100, 200]], upload_time=2
    )

    result = _list_files("thruster", 11, 200, require_coverage=True)
    assert result["statusCode"] == 200
    assert sorted(json.loads(result["body"])) == [
        "imap_2026_273_2026_273_hist_02.sff",
        "imap_2026_273_2026_274_hist_01.sff",
    ]


@pytest.mark.parametrize(
    ("kernel_type", "filename"),
    [
        ("spin", "imap_2026_267_2026_267_01.spin.csv"),
        ("thruster", "imap_2026_273_2026_273_hist_01.sff"),
    ],
)
def test_spice_query_spin_and_thruster(session, kernel_type, filename):
    """The SPICE query API returns spin and small forces files by type."""
    _insert_test_file(session, filename, [[0, 300]])
    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": kernel_type, "latest": "true"}}, None
    )
    assert [row["kernel_type"] for row in json.loads(result["body"])] == [kernel_type]
