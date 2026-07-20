"""Tests for the Query API."""

import datetime
import json

import pytest

from sds_data_manager.lambda_code.SDSCode.api_lambdas import query_api
from sds_data_manager.lambda_code.SDSCode.database import models


def param_not_valid_in_response(response_body, param, table):
    """Check if error message contains expected content for invalid parameter."""
    error_msg = f"{param} is not a valid query parameter for {table} table"
    return error_msg in response_body


def _populate_test_data(session):
    """Put a filepath into the test data."""
    filepath = "test/file/path/imap_hit_l0_raw_20251107_v001.pkts"

    metadata_params = {
        "file_path": filepath,
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20251107", "%Y%m%d"),
        "major_version": 1,
        "minor_version": 1,
        "extension": "pkts",
        "ingestion_date": datetime.datetime.strptime(
            "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
        "released": True,
    }

    # Add data to the ScienceFiles table and return the session
    session.add(models.ScienceFiles(**metadata_params))
    session.commit()


@pytest.fixture
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
                "major_version": 1,
                "minor_version": 1,
                "version": "v001.0001",
                "extension": "pkts",
                "ingestion_date": "20251107 10:13:12",
                "cr": None,
                "crid": None,
                "released": True,
            }
        ]
    )
    return expected_response


def test_query_result_body(session):
    """Tests that the query result body can be loaded."""
    _populate_test_data(session)
    event = {"queryStringParameters": {}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert json.loads(returned_query["body"])


def test_query_result_header(session):
    """Tests that the query result header is json."""
    _populate_test_data(session)
    event = {"queryStringParameters": {}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["headers"] is not None
    assert returned_query["headers"]["Content-Type"] == "application/json"


def test_start_date_query(session, expected_response):
    """Test that start date can be queried."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"start_date": "20251101"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_end_date_query(session, expected_response):
    """Test that end date can be queried."""
    _populate_test_data(session)
    event = {
        "queryStringParameters": {"start_date": "20251101"},
    }
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_start_and_end_date_query(session, expected_response):
    """Test that both start and end date can be queried."""
    event = {
        "queryStringParameters": {"start_date": "20251101", "end_date": "20251201"}
    }
    _populate_test_data(session)
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_empty_start_date_query(session):
    """Test that a start_date query with no matches returns an empty list."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"start_date": "20261101"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_end_date_query(session):
    """Test that an end_date query with no matches returns an empty list."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"start_date": "20261101"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_non_date_query(session, expected_response):
    """Test that a non-date parameters can be queried."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"instrument": "hit"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_ingestion_start_date_query(session, expected_response):
    """Test that ingestion_start_date can be queried."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"ingestion_start_date": "20251107"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_ingestion_end_date_query(session, expected_response):
    """Test that ingestion_end_date can be queried."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"ingestion_end_date": "20251107"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_ingestion_start_and_end_date_query(session, expected_response):
    """Test that both ingestion_start_date and ingestion_end_date can be queried."""
    _populate_test_data(session)
    event = {
        "queryStringParameters": {
            "ingestion_start_date": "20251106",
            "ingestion_end_date": "20251108",
        }
    }

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_empty_ingestion_start_date_query(session):
    """Test that an ingestion_start_date query with no matches returns an empty list."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"ingestion_start_date": "20261101"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_ingestion_end_date_query(session):
    """Test that an ingestion_end_date query with no matches returns an empty list."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"ingestion_end_date": "20251101"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_ingestion_start_and_end_date_query(session):
    """Test that ingestion params with no matches returns an empty list."""
    _populate_test_data(session)
    event = {
        "queryStringParameters": {
            "ingestion_start_date": "20261101",
            "ingestion_end_date": "20261110",
        }
    }
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_non_date_query(session):
    """Test that a non-date query with no matches returns an empty list."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"data_level": "l2"}}
    expected_response = json.dumps([])
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_multi_param_query(session, expected_response):
    """Test that multiple parameters can be queried."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"instrument": "hit", "data_level": "l0"}}

    returned_query = query_api.lambda_handler(event=event, context={})
    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(expected_response)


def test_invalid_query(session):
    """Test that invalid parameters return a 400 status with explanation."""
    _populate_test_data(session)
    event = {"queryStringParameters": {"size": "500"}}
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    # Check if error message contains the expected content
    assert param_not_valid_in_response(returned_query["body"], "size", "science")


def test_end_date_not_valid_for_spice(session):
    """spice has no start_date, so the synthetic end_date param is rejected (400).

    Regression test: end_date used to be offered for every non-ancillary table
    and was implemented as a `start_date <=` filter, so ?table=spice&end_date=...
    raised AttributeError (500) instead of a clean validation error.
    """
    event = {"queryStringParameters": {"table": "spice", "end_date": "20250101"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert param_not_valid_in_response(returned_query["body"], "end_date", "spice")


def _populate_test_data_ancillary_table(session):
    """Put a filepath into the test data for the ancillary table."""
    filepath = "test/ancillary/file/path/imap_mag_test_20210101_v001.csv"

    metadata_params = {
        "file_path": filepath,
        "instrument": "mag",
        "descriptor": "test",
        "start_date": datetime.datetime.strptime("20210101", "%Y%m%d"),
        "version": "v001",
        "extension": "csv",
        "ingestion_date": datetime.datetime.strptime(
            "2021-01-01 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
        "released": True,
    }

    # Add data to the AncillaryFiles table and return the session
    session.add(models.AncillaryFiles(**metadata_params))
    session.commit()


@pytest.fixture
def expected_response_ancillary_table():
    """Return the expected response for ancillary table."""
    expected_response = json.dumps(
        [
            {
                "file_path": "test/ancillary/file/path/imap_mag_test_20210101_v001.csv",
                "instrument": "mag",
                "descriptor": "test",
                "start_date": "20210101",
                "end_date": None,
                "version": "v001",
                "extension": "csv",
                "ingestion_date": "20210101 10:13:12",
                "released": True,
            }
        ]
    )
    return expected_response


def test_query_result_body_ancillary_table(session):
    """Tests that the query result body can be loaded for ancillary table."""
    _populate_test_data_ancillary_table(session)
    event = {"queryStringParameters": {"table": "ancillary"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert json.loads(returned_query["body"])


def test_query_ancillary_table(session, expected_response_ancillary_table):
    """Test querying the ancillary table with a valid parameter."""
    _populate_test_data_ancillary_table(session)

    event = {"queryStringParameters": {"instrument": "mag", "table": "ancillary"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Parse both JSON objects and compare the data rather than the string representation
    assert json.loads(returned_query["body"]) == json.loads(
        expected_response_ancillary_table
    )


def test_invalid_param_ancillary_query(session):
    """Test invalid parameter on the ancillary table."""
    _populate_test_data_ancillary_table(session)

    event = {"queryStringParameters": {"repointing": "123", "table": "ancillary"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    # Check if error message contains the expected content
    assert param_not_valid_in_response(
        returned_query["body"], "repointing", "ancillary"
    )


def _populate_versioned_science_data(session):
    """Populate one science series with several major/minor versions.

    All rows share the same instrument/level/descriptor/start_date series so
    that version resolution collapses them. Versions added (major, minor):
    (1, 1), (1, 2), (2, 1), (2, 3).
    """
    versions = [(1, 1), (1, 2), (2, 1), (2, 3)]
    for major, minor in versions:
        filepath = (
            f"test/file/path/imap_hit_l1a_count_20251107_v{major:03d}.{minor:04d}.cdf"
        )
        session.add(
            models.ScienceFiles(
                file_path=filepath,
                instrument="hit",
                data_level="l1a",
                descriptor="count",
                start_date=datetime.datetime.strptime("20251107", "%Y%m%d"),
                major_version=major,
                minor_version=minor,
                extension="cdf",
                ingestion_date=datetime.datetime.strptime(
                    "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                released=True,
            )
        )
    session.commit()


def _returned_versions(returned_query):
    """Return the set of (major_version, minor_version) tuples in a response."""
    return {
        (item["major_version"], item["minor_version"])
        for item in json.loads(returned_query["body"])
    }


def test_default_returns_latest_major_all_minors(session):
    """Omitting version params returns the latest major with all its minors."""
    _populate_versioned_science_data(session)
    event = {"queryStringParameters": {"instrument": "hit"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Latest major is 2; both minor versions of major 2 are returned.
    assert _returned_versions(returned_query) == {(2, 1), (2, 3)}


def test_latest_true_returns_single_newest_file(session):
    """latest=true returns only the single newest file (latest major+minor)."""
    _populate_versioned_science_data(session)
    event = {"queryStringParameters": {"instrument": "hit", "latest": "true"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert _returned_versions(returned_query) == {(2, 3)}


def test_concrete_major_version_filter(session):
    """A concrete major_version returns all minors of that major only."""
    _populate_versioned_science_data(session)
    event = {"queryStringParameters": {"instrument": "hit", "major_version": "1"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert _returned_versions(returned_query) == {(1, 1), (1, 2)}


def test_version_alias_minor_only(session):
    """Legacy `version=vXXX` maps to a minor_version filter within latest major."""
    _populate_versioned_science_data(session)
    event = {"queryStringParameters": {"instrument": "hit", "version": "v001"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # minor_version=1 within the latest major (2).
    assert _returned_versions(returned_query) == {(2, 1)}


def test_version_alias_full_form(session):
    """Legacy `version=vMMM.mmmm` maps to both major and minor filters."""
    _populate_versioned_science_data(session)
    event = {"queryStringParameters": {"instrument": "hit", "version": "v001.0002"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert _returned_versions(returned_query) == {(1, 2)}


def _populate_old_filename_science_data(session):
    """Populate science files that use the legacy vXXX filename convention.

    Files produced before the major/minor version split used filenames like
    ``imap_hit_l1a_count_20251107_v002.cdf`` and are stored with the default
    major_version of 1 and the legacy number as the minor_version.
    """
    for minor in (1, 2):
        filepath = f"test/file/path/imap_hit_l1a_count_20251107_v{minor:03d}.cdf"
        session.add(
            models.ScienceFiles(
                file_path=filepath,
                instrument="hit",
                data_level="l1a",
                descriptor="count",
                start_date=datetime.datetime.strptime("20251107", "%Y%m%d"),
                major_version=1,
                minor_version=minor,
                extension="cdf",
                ingestion_date=datetime.datetime.strptime(
                    "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                released=True,
            )
        )
    session.commit()


def test_query_with_old_filename_format(session):
    """Files from legacy vXXX filenames stay queryable, including via `version`."""
    _populate_old_filename_science_data(session)

    # A plain query returns the files (the default latest-major path still works;
    # the legacy files all share major_version 1).
    event = {"queryStringParameters": {"instrument": "hit"}}
    returned_query = query_api.lambda_handler(event=event, context={})
    assert returned_query["statusCode"] == 200
    assert _returned_versions(returned_query) == {(1, 1), (1, 2)}

    # The legacy `version=vXXX` parameter still resolves to a specific file.
    event = {"queryStringParameters": {"instrument": "hit", "version": "v002"}}
    returned_query = query_api.lambda_handler(event=event, context={})
    assert returned_query["statusCode"] == 200
    assert _returned_versions(returned_query) == {(1, 2)}


def test_latest_default_excludes_unreleased_for_unauthenticated(session):
    """Unauthenticated latest resolution ignores unreleased newer versions."""
    _populate_versioned_science_data(session)
    # Add an unreleased newer major that an unauthenticated user must not see,
    # and which must not suppress the latest released version.
    session.add(
        models.ScienceFiles(
            file_path="test/file/path/imap_hit_l1a_count_20251107_v003.0001.cdf",
            instrument="hit",
            data_level="l1a",
            descriptor="count",
            start_date=datetime.datetime.strptime("20251107", "%Y%m%d"),
            major_version=3,
            minor_version=1,
            extension="cdf",
            ingestion_date=datetime.datetime.strptime(
                "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            released=False,
        )
    )
    session.commit()

    event = {"queryStringParameters": {"instrument": "hit"}}
    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    # Latest *released* major is still 2, with both of its minors.
    assert _returned_versions(returned_query) == {(2, 1), (2, 3)}


def test_invalid_version_alias_returns_400(session):
    """A malformed `version` value returns a 400."""
    _populate_versioned_science_data(session)
    event = {"queryStringParameters": {"instrument": "hit", "version": "vABC"}}

    returned_query = query_api.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
