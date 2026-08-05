"""Tests for the SPICE Query API."""

import json
from datetime import datetime

import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_query_api
from sds_data_manager.lambda_code.SDSCode.database import models


def _insert_ck_test_data(session):
    """Put a filepath into the test data."""
    metadata_params = {
        "file_name": "imap_2025_118_2025_120_009.ah.bc",
        "file_path": "imap/spice/ck/imap_2025_118_2025_120_009.ah.bc",
        "file_root": "imap_2025_118_2025_120_.ah.bc",
        "kernel_type": "attitude_history",
        "version": 9,
        "min_date_j2000": 799240876.0732585,
        "max_date_j2000": 799244783.0732579,
        "file_intervals_j2000": [
            [799240876.0732585, 799240921.0732585],
            [799242436.0732583, 799244783.0732579],
        ],
        "min_date_datetime": datetime(2025, 4, 29, 23, 20, 6),
        "max_date_datetime": datetime(2025, 4, 30, 0, 25, 13),
        "file_intervals_datetime": [
            ["2025-04-29T23:20:06.887765+00:00", "2025-04-29T23:20:51.887765+00:00"],
            ["2025-04-29T23:46:06.887765+00:00", "2025-04-30T00:25:13.887765+00:00"],
        ],
        "min_date_sclk": "1/0512204570:32482",
        "max_date_sclk": "1/0512208477:32482",
        "file_intervals_sclk": [
            ["1/0512204570:32482", "1/0512204615:32482"],
            ["1/0512206130:32482", "1/0512208477:32482"],
        ],
        "sclk_kernel": "naif0012.tls",
        "lsk_kernel": "imap_sclk_0012.tsc",
        "ingestion_date": datetime(2025, 4, 9, 21, 12, 53),
    }

    # Add data to the ScienceFiles table and return the session
    session.add(models.SPICEFiles(**metadata_params))
    session.commit()


def _insert_many_versions_test_data(session):
    """Put a filepath into the test data."""
    for version in range(0, 10):
        metadata_params = {
            "file_name": f"imap_2025_118_2025_120_00{version}.ah.bc",
            "file_path": f"imap/spice/ck/imap_2025_118_2025_120_00{version}.ah.bc",
            "file_root": "imap_2025_118_2025_120_.ah.bc",
            "kernel_type": "attitude_history",
            "version": version,
            "min_date_j2000": 799240876.0732585,
            "max_date_j2000": 799244783.0732579,
            "file_intervals_j2000": [
                [799240876.0732585, 799240921.0732585],
                [799242436.0732583, 799244783.0732579],
            ],
            "min_date_datetime": datetime(2025, 4, 29, 23, 20, 6),
            "max_date_datetime": datetime(2025, 4, 30, 0, 25, 13),
            "file_intervals_datetime": [
                [
                    "2025-04-29T23:20:06.887765+00:00",
                    "2025-04-29T23:20:51.887765+00:00",
                ],
                [
                    "2025-04-29T23:46:06.887765+00:00",
                    "2025-04-30T00:25:13.887765+00:00",
                ],
            ],
            "min_date_sclk": "1/0512204570:32482",
            "max_date_sclk": "1/0512208477:32482",
            "file_intervals_sclk": [
                ["1/0512204570:32482", "1/0512204615:32482"],
                ["1/0512206130:32482", "1/0512208477:32482"],
            ],
            "sclk_kernel": "naif0012.tls",
            "lsk_kernel": "imap_sclk_0012.tsc",
            "ingestion_date": datetime(2025, 4, 9, 21, 12, 53),
        }

        # Add data to the ScienceFiles table and return the session
        session.add(models.SPICEFiles(**metadata_params))
        session.commit()


@pytest.fixture
def expected_ck_response():
    """Return the expected response."""
    expected_response = json.dumps(
        [
            {
                "file_name": "ck/imap_2025_118_2025_120_009.ah.bc",
                "file_root": "imap_2025_118_2025_120_.ah.bc",
                "kernel_type": "attitude_history",
                "version": 9,
                "min_date_j2000": 799240876.0732585,
                "max_date_j2000": 799244783.0732579,
                "file_intervals_j2000": [
                    [799240876.0732585, 799240921.0732585],
                    [799242436.0732583, 799244783.0732579],
                ],
                "min_date_datetime": "2025-04-29, 23:20:06",
                "max_date_datetime": "2025-04-30, 00:25:13",
                "file_intervals_datetime": [
                    [
                        "2025-04-29T23:20:06.887765+00:00",
                        "2025-04-29T23:20:51.887765+00:00",
                    ],
                    [
                        "2025-04-29T23:46:06.887765+00:00",
                        "2025-04-30T00:25:13.887765+00:00",
                    ],
                ],
                "min_date_sclk": "1/0512204570:32482",
                "max_date_sclk": "1/0512208477:32482",
                "file_intervals_sclk": [
                    ["1/0512204570:32482", "1/0512204615:32482"],
                    ["1/0512206130:32482", "1/0512208477:32482"],
                ],
                "sclk_kernel": "naif0012.tls",
                "lsk_kernel": "imap_sclk_0012.tsc",
                "ingestion_date": "2025-04-09, 21:12:53",
                "timestamp": 1744233173.0,
            }
        ]
    )
    return expected_response


def test_query_result_body(session):
    """Tests that the query result body can be loaded."""
    _insert_ck_test_data(session)
    event = {"queryStringParameters": {}}

    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert json.loads(returned_query["body"])


def test_file_name_query(session, expected_ck_response):
    """Test that start date can be queried."""
    _insert_ck_test_data(session)
    event = {"queryStringParameters": {"file_name": "imap_2025_118_2025_120_009.ah.bc"}}

    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_ck_response


def test_start_time_query(session, expected_ck_response):
    """Test that start date can be queried."""
    _insert_ck_test_data(session)
    event = {"queryStringParameters": {"start_time": "0"}}

    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_ck_response


def test_end_time_query(session, expected_ck_response):
    """Test that end date can be queried."""
    _insert_ck_test_data(session)
    event = {
        "queryStringParameters": {"end_time": "1000000000"},
    }
    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_ck_response


def test_start_and_end_time_query(session, expected_ck_response):
    """Test that both start and end date can be queried."""
    event = {"queryStringParameters": {"start_time": "0", "end_time": "1000000000"}}
    _insert_ck_test_data(session)
    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_ck_response


def test_empty_start_time_query(session):
    """Test that a start_date query with no matches returns an empty list."""
    _insert_ck_test_data(session)
    event = {"queryStringParameters": {"start_time": "1000000000"}}
    expected_response = json.dumps([])
    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_end_date_query(session):
    """Test that an end_time query with no matches returns an empty list."""
    _insert_ck_test_data(session)
    event = {"queryStringParameters": {"end_time": "0"}}
    expected_response = json.dumps([])
    returned_query = spice_query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_invalid_query(session):
    """Test that invalid parameters return a 400 status with explanation."""
    _insert_ck_test_data(session)
    event = {"queryStringParameters": {"size": "500"}}
    expected_response = json.dumps(
        "size is not a valid query parameter. "
        + "Valid query parameters are: "
        + "['file_name', 'start_time', 'end_time', 'type',"
        + " 'latest', 'start_ingest_date', 'end_ingest_date']"
    )
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert returned_query["statusCode"] == 400
    assert returned_query["body"] == expected_response


def test_latest_query(session, expected_ck_response):
    """Test 'latest' filters out older versions."""
    _insert_many_versions_test_data(session)

    # First, assert all 10 would be returned with no filter
    event = {"queryStringParameters": {}}
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert len(json.loads(returned_query["body"])) == 10

    # Next, assert that only one returns if latest=True
    event = {"queryStringParameters": {"latest": "True"}}
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert returned_query["body"] == expected_ck_response


def test_ingest_time_queries(session):
    """Test 'start_ingest_date' and 'end_ingest_date' for accuracy."""
    _insert_ck_test_data(session)

    # This event *does not* contain the time range of the test ck file
    event = {
        "queryStringParameters": {
            "start_ingest_date": "20220101",
            "end_ingest_date": "20230101",
        }
    }
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert len(json.loads(returned_query["body"])) == 0

    # This event *does* contain the time range of the test ck file
    event = {
        "queryStringParameters": {
            "start_ingest_date": "20250401",
            "end_ingest_date": "20250415",
        }
    }
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert len(json.loads(returned_query["body"])) == 1


def test_no_type_query(session, expected_ck_response):
    """Test query parameter with no `type`."""
    _insert_ck_test_data(session)

    event = {"queryStringParameters": {}}
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert returned_query["body"] == expected_ck_response


def test_type_query_kernels(session, expected_ck_response):
    """Test if the `type` query parameter works with `kernels` type."""
    _insert_ck_test_data(session)

    event = {"queryStringParameters": {"type": "kernels"}}
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert returned_query["body"] == expected_ck_response


def test_type_query_attitude_history(session, expected_ck_response):
    """Test if the `type` query parameter works with `attitude_history` type."""
    _insert_ck_test_data(session)

    event = {"queryStringParameters": {"type": "attitude_history"}}
    returned_query = spice_query_api.lambda_handler(event=event, context={})
    assert returned_query["body"] == expected_ck_response
