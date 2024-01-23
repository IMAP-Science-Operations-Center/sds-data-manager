"""Test queries lambda"""
import datetime
import json

import pytest
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode import queries
from sds_data_manager.lambda_code.SDSCode.database import models


@pytest.fixture()
def setup_test_data(test_engine):
    metadata_params = {
        "file_path": "test/file/path/imap_hit_l0_raw_20251107_20251108_v02-01.pkts",
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20251107", "%Y%m%d"),
        "end_date": datetime.datetime.strptime("20251108", "%Y%m%d"),
        "version": "v02-01",
        "extension": "pkts",
    }

    # Add data to the file catalog
    session = Session(test_engine)
    session.add(models.FileCatalog(**metadata_params))
    session.commit()

    yield session
    session.close()


@pytest.fixture()
def expected_response():
    expected_response = json.dumps(
        str(
            [
                (
                    1,
                    "test/file/path/imap_hit_l0_raw_20251107_20251108_v02-01.pkts",
                    "hit",
                    "l0",
                    "raw",
                    datetime.datetime(2025, 11, 7, 0, 0),
                    datetime.datetime(2025, 11, 8, 0, 0),
                    "v02-01",
                    "pkts",
                    None,
                )
            ]
        )
    )
    return expected_response


def test_start_date_query(setup_test_data, test_engine, expected_response):
    """Test that start date can be queried"""
    event = {"queryStringParameters": {"start_date": "20251101"}}

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_end_date_query(setup_test_data, test_engine, expected_response):
    """Test that end date can be queried"""
    event = {
        "queryStringParameters": {"start_date": "20251101"},
    }
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_start_and_end_date_query(setup_test_data, test_engine, expected_response):
    "test that both start and end date can be queried"
    event = {
        "queryStringParameters": {"start_date": "20251101", "end_date": "20251201"}
    }

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_start_date_query(setup_test_data, test_engine):
    "Test that a start_date query with no matches returns an empty list"
    event = {"queryStringParameters": {"start_date": "20261101"}}
    expected_response = json.dumps("[]")
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_end_date_query(setup_test_data, test_engine):
    "Test that an end_date query with no matches returns an empty list"
    event = {"queryStringParameters": {"start_date": "20261101"}}
    expected_response = json.dumps("[]")
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_non_date_query(setup_test_data, test_engine):
    "Test that a non-date query with no matches returns an empty list"
    event = {"queryStringParameters": {"data_level": "l2"}}
    expected_response = json.dumps("[]")
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_non_date_query(setup_test_data, test_engine, expected_response):
    """Test that a non-date parameters can be queried"""
    event = {"queryStringParameters": {"instrument": "hit"}}

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_multi_param_query(setup_test_data, test_engine, expected_response):
    "Test that multiple parameters can be queried"
    event = {"queryStringParameters": {"instrument": "hit", "data_level": "l0"}}

    returned_query = queries.lambda_handler(event=event, context={})
    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_invalid_query(setup_test_data, test_engine):
    "Test that invalid parameters return a 400 status with explanation"
    event = {"queryStringParameters": {"size": "500"}}
    expected_response = json.dumps(
        "size is not a valid query parameter. "
        + "Valid query parameters are: "
        + "['file_path', 'instrument', 'data_level', 'descriptor', "
        "'start_date', 'end_date', 'version', 'extension']"
    )
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert returned_query["body"] == expected_response
