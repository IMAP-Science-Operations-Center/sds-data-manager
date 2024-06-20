"""Tests for the Query API."""

import datetime
import json

import pytest
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode import query_api
from sds_data_manager.lambda_code.SDSCode.database import models


@pytest.fixture()
def setup_test_data(test_engine):
    """Return a database engine to test with."""
    filepath = "test/file/path/imap_hit_l0_raw_20251107_v001.pkts"

    metadata_params = {
        "file_path": filepath,
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20251107", "%Y%m%d"),
        "version": "v001",
        "extension": "pkts",
        "ingestion_date": datetime.datetime.strptime(
            "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
    }

    # Add data to the file catalog and return the session
    with Session(test_engine) as session:
        session.add(models.FileCatalog(**metadata_params))
        session.commit()

        yield session


@pytest.fixture()
def expected_response():
    """Return the expected response."""
    expected_response = json.dumps(
        [
            {
                "file_path": "test/file/path/imap_hit_l0_raw_20251107_v001.pkts",
                "instrument": "hit",
                "data_level": "l0",
                "descriptor": "raw",
                "start_date": "20251107",
                "repointing": None,
                "version": "v001",
                "extension": "pkts",
                "ingestion_date": "2025-11-07 10:13:12",
            }
        ]
    )
    return expected_response


def test_query_result_body(setup_test_data):
    """Tests that the query result body can be loaded."""
    event = {"queryStringParameters": {}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert json.loads(returned_query["body"])


def test_start_date_query(setup_test_data, test_engine, expected_response):
    """Test that start date can be queried."""
    event = {"queryStringParameters": {"start_date": "20251101"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_end_date_query(setup_test_data, test_engine, expected_response):
    """Test that end date can be queried."""
    event = {
        "queryStringParameters": {"start_date": "20251101"},
    }
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_start_and_end_date_query(setup_test_data, test_engine, expected_response):
    """Test that both start and end date can be queried."""
    event = {
        "queryStringParameters": {"start_date": "20251101", "end_date": "20251201"}
    }

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_start_date_query(setup_test_data, test_engine):
    """Test that a start_date query with no matches returns an empty list."""
    event = {"queryStringParameters": {"start_date": "20261101"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_end_date_query(setup_test_data, test_engine):
    """Test that an end_date query with no matches returns an empty list."""
    event = {"queryStringParameters": {"start_date": "20261101"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_non_date_query(setup_test_data, test_engine):
    """Test that a non-date query with no matches returns an empty list."""
    event = {"queryStringParameters": {"data_level": "l2"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_non_date_query(setup_test_data, test_engine, expected_response):
    """Test that a non-date parameters can be queried."""
    event = {"queryStringParameters": {"instrument": "hit"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_multi_param_query(setup_test_data, test_engine, expected_response):
    """Test that multiple parameters can be queried."""
    event = {"queryStringParameters": {"instrument": "hit", "data_level": "l0"}}

    returned_query = query_api.lambda_handler(event=event, context={})
    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_invalid_query(setup_test_data, test_engine):
    """Test that invalid parameters return a 400 status with explanation."""
    event = {"queryStringParameters": {"size": "500"}}
    expected_response = json.dumps(
        "size is not a valid query parameter. "
        + "Valid query parameters are: "
        + "['file_path', 'instrument', 'data_level', 'descriptor', "
        "'start_date', 'repointing', 'version', 'extension', 'ingestion_date', "
        + "'end_date']"
    )
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert returned_query["body"] == expected_response


def test_sorting_of_query(setup_test_data):
    """Add another file that should be sorted before the original file."""
    metadata_params2 = {
        "file_path": "test/file/path/imap_hit_l0_raw_20251106_v001.pkts",
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20251106", "%Y%m%d"),
        "version": "v001",
        "extension": "pkts",
        "ingestion_date": datetime.datetime.strptime(
            "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
    }

    expected_response = json.dumps(
        [
            {
                "file_path": "test/file/path/imap_hit_l0_raw_20251106_v001.pkts",
                "instrument": "hit",
                "data_level": "l0",
                "descriptor": "raw",
                "start_date": "20251106",
                "repointing": None,
                "version": "v001",
                "extension": "pkts",
                "ingestion_date": "2025-11-07 10:13:12",
            },
            {
                "file_path": "test/file/path/imap_hit_l0_raw_20251107_v001.pkts",
                "instrument": "hit",
                "data_level": "l0",
                "descriptor": "raw",
                "start_date": "20251107",
                "repointing": None,
                "version": "v001",
                "extension": "pkts",
                "ingestion_date": "2025-11-07 10:13:12",
            },
        ]
    )

    # Add data to the file catalog
    session = setup_test_data
    session.add(models.FileCatalog(**metadata_params2))
    session.commit()

    event = {"queryStringParameters": {"start_date": "20251101"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response
