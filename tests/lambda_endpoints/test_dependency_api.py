"""Test data dependency functions."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest
from imap_data_access.processing_input import (
    AncillaryInput,
    ProcessingInputCollection,
    ScienceInput,
)

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dependency
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import get_files
from tests.lambda_endpoints.conftest import (
    _populate_file_catalog,
    create_dependency_api_event,
)

#####################################
# ERROR STATUS CODE TESTS
#####################################


def test_lambda_handler_no_dependencies():
    """Test lambda_handler when no dependencies are found."""
    event = {
        "queryStringParameters": {
            "data_source": "nonexistent",
            "data_type": "l0",
            "descriptor": "raw",
            "dependency_type": "UPSTREAM",
            "relationship": "HARD",
        }
    }

    response = dependency.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert response["body"] == "[]"


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency.get_dependencies"
)
def test_lambda_handler_invalid_dependency_type(mock_get_dependencies):
    """Test lambda_handler when invalid dependency type is provided."""
    event = {
        "queryStringParameters": {
            "data_source": "jim",
            "data_type": "l0",
            "descriptor": "raw",
            "dependency_type": "INVALID",
            "relationship": "HARD",
        }
    }
    mock_get_dependencies.return_value = None

    response = dependency.lambda_handler(event, None)

    assert response["statusCode"] == 500
    assert response["body"] == "Failed to load dependencies"


def test_missing_dependency(session):
    """Test that 206 error is returned."""
    event = create_dependency_api_event(
        "swe", "l1b", start_date="20240104", version="v001", trigger_type="ancillary"
    )
    dependency_response = dependency.lambda_handler(event, None)

    assert dependency_response["statusCode"] == 200
    assert dependency_response["body"] == "[]"


def test_missing_required_params():
    """Test that 400 error is returned."""
    event = {
        "queryStringParameters": {
            "dependency_type": "DOWNSTREAM",
            "relationship": "HARD",
            "data_source": "swe",
            "data_type": "l1b",
            "descriptor": "sci",
            "start_date": "20240104",
            "version": "v001",
        }
    }
    dependency_response = dependency.lambda_handler(event, None)
    assert dependency_response["statusCode"] == 400
    assert dependency_response["body"] == (
        "trigger_type not found. If 'start_date' is"
        " supplied, 'trigger_type' is required."
    )
    event["queryStringParameters"].pop("version")
    dependency_response = dependency.lambda_handler(event, None)
    assert dependency_response["statusCode"] == 400
    assert dependency_response["body"] == (
        "Version not found. If 'start_date' is" " supplied, 'version' is required."
    )


#####################################
# LAMBDA HANDLER TESTS
#####################################
def test_get_downstream_dependencies():
    """Tests get_downstream_dependencies function."""
    event = create_dependency_api_event("hit", "l1a", "counts")

    dependency_response = dependency.lambda_handler(event, None)
    dependents = json.loads(dependency_response["body"])

    expected_complete_dependent = [
        {
            "data_source": "hit",
            "data_type": "l1b",
            "descriptor": "all",
        }
    ]
    assert len(dependents) == 1

    assert dependents == expected_complete_dependent

    # Add test for getting back ancillary dependency
    event = create_dependency_api_event("swe", "l1b", dep_type="UPSTREAM")
    dependency_response = dependency.lambda_handler(event, None)
    dependents = json.loads(dependency_response["body"])

    expected_complete_dependent = [
        {
            "data_source": "swe",
            "data_type": "l1a",
            "descriptor": "sci",
        },
        {
            "data_source": "swe",
            "data_type": "ancillary",
            "descriptor": "l1b-in-flight-cal",
        },
    ]
    assert len(dependents) == 2
    assert dependents == expected_complete_dependent


def test_get_upstream_ancillary_trigger(session, caplog):
    """Tests get upstream dependencies with an ancillary trigger source."""
    _populate_file_catalog(session)
    event = create_dependency_api_event(
        "swe",
        "l1b",
        dep_type="UPSTREAM",
        start_date="20231230",
        version="v001",
        trigger_type="ancillary",
    )
    dependency_response = dependency.lambda_handler(event, None)
    dependencies = dependency_response["body"]
    # There are three swe l1a records before 20240104, but one of them was filtered
    # out because the swe l1b downstream dependency for that date and version
    # was already processed, so it is not included in the output.
    science_in = ScienceInput(
        "imap_swe_l1a_sci_20240101_v001.cdf",
        "imap_swe_l1a_sci_20240103_v001.cdf",
    )
    ancillary_in = AncillaryInput("imap_swe_l1b-in-flight-cal_20230101_v001.cdf")
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(science_in, ancillary_in)

    assert dependencies == expected_processing_input.serialize()
    # Move start_date forward by one and we should now have 2 ancillary files.
    event["queryStringParameters"]["start_date"] = "20231231"
    dependency_response = dependency.lambda_handler(event, None)
    dependencies = dependency_response["body"]
    ancillary_in = AncillaryInput(
        "imap_swe_l1b-in-flight-cal_20230101_v001.cdf",
        "imap_swe_l1b-in-flight-cal_20231231_20240102_v002.cdf",
    )
    expected_processing_input = ProcessingInputCollection(science_in, ancillary_in)
    assert dependencies == expected_processing_input.serialize()


def test_get_upstream_science_trigger(session):
    """Tests get upstream dependencies with a science file as the trigger source."""
    _populate_file_catalog(session)
    event = create_dependency_api_event(
        "swe",
        "l1b",
        dep_type="UPSTREAM",
        start_date="20240103",
        version="v001",
        trigger_type="l1a",
    )
    dependency_response = dependency.lambda_handler(event, None)
    dependencies = dependency_response["body"]
    # There are three swe l1a records, but since the trigger is the same source as
    # the upstream source, then the exact date is used to find the swe l1a file.
    science_in = ScienceInput("imap_swe_l1a_sci_20240103_v001.cdf")
    ancillary_in = AncillaryInput("imap_swe_l1b-in-flight-cal_20230101_v001.cdf")
    # Expect ancillary dependencies and science dependencies
    expected_processing_input = ProcessingInputCollection(science_in, ancillary_in)

    assert dependencies == expected_processing_input.serialize()


@patch.object(dependency.DependencyConfig, "_load_dependencies")
def test_dependency_class(mock_load_dependencies):
    """Test DependencyConfig class."""
    # Set side effect to return value error of product not having
    # valid source, type, and descriptor.
    msg = "Data product must have: (source, type, descriptor)"
    mock_load_dependencies.side_effect = ValueError(msg)

    with pytest.raises(
        ValueError, match="Data product must have: \\(source, type, descriptor\\)"
    ):
        dependency.DependencyConfig()


#####################################
# GET_FILES() TESTS
#####################################
def test_get_primary_science_files(session):
    """Tests the get_file function for science files."""
    _populate_file_catalog(session)

    dep = {"data_source": "ultra", "data_type": "l2", "descriptor": "sci"}
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        version="v001",
        primary_sci_dep=True,
    )[0]

    assert record.instrument == "ultra"
    assert record.data_level == "l2"
    assert record.descriptor == "sci"
    assert record.start_date == datetime(2024, 1, 1)
    assert record.version == "v001"

    # Non-existent record should return an empty list
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    assert record == []


def test_get_science_files_date_range(session):
    """Tests the get_file function for science files dependent on start_date."""
    _populate_file_catalog(session)
    # Test with end date
    # It should return two swe records
    dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
    records_1 = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 2),
        version="v001",
        end_date=datetime(2024, 1, 3),
        primary_sci_trigger=False,
        primary_sci_dep=True,
    )
    assert len(records_1) == 2

    # Test with no end date
    # It should return three swe records
    records_2 = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        version="v001",
        primary_sci_trigger=False,
        primary_sci_dep=True,
    )
    assert len(records_2) == 3


def test_get_ancillary_files(session):
    """Tests the get_file function."""
    _populate_file_catalog(session)

    dep = {
        "data_source": "swe",
        "data_type": "ancillary",
        "descriptor": "l1b-in-flight-cal",
    }
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2023, 1, 1),
        version="v001",
    )[0]
    assert record.instrument == "swe"
    assert record.descriptor == "l1b-in-flight-cal"
    assert record.start_date == datetime(2023, 1, 1)
    assert record.version == "v001"

    # Get ancillary file covering range
    dep = {
        "data_source": "swe",
        "data_type": "ancillary",
        "descriptor": "l1b-in-flight-cal",
    }
    start_date = datetime(2024, 1, 1)
    record = get_files(
        session,
        dependency=dep,
        start_date=start_date,
        version="v001",
    )[1]
    assert record.instrument == "swe"
    assert record.descriptor == "l1b-in-flight-cal"
    assert record.start_date <= start_date
    assert record.end_date >= start_date
    assert record.version == "v001"
    # Non-existent record should an empty list
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2000, 1, 1),
        version="v001",
    )
    assert record == []


def test_get_exact_date_science_files(session):
    """Tests the get_file function."""
    _populate_file_catalog(session)

    dep = {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"}
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2024, 1, 1),
        version="v001",
        primary_sci_trigger=True,
    )
    assert len(record) == 1
    record = record[0]
    assert record.instrument == "swe"
    assert record.descriptor == "sci"
    assert record.start_date == datetime(2024, 1, 1)
    assert record.version == "v001"


def test_get_files_exact_version(session):
    """Test get_files returns the exact version."""
    _populate_file_catalog(session)
    dep = {"data_source": "lo", "data_type": "l1a", "descriptor": "sci"}
    record = get_files(
        session,
        dependency=dep,
        start_date=datetime(2010, 1, 1),
        version="v001",
        primary_sci_trigger=True,
    )

    assert len(record) == 1
    record = record[0]
    assert record.instrument == "lo"
    assert record.descriptor == "sci"
    assert record.start_date == datetime(2010, 1, 1)
    assert record.version == "v001"


def test_get_files_latest_version(session):
    """Test get_files returns the latest version."""
    _populate_file_catalog(session)
    dep = {"data_source": "lo", "data_type": "l1a", "descriptor": "sci"}
    records = get_files(
        session,
        dependency=dep,
        start_date=datetime(2010, 1, 2),
        version="v001",
        primary_sci_trigger=False,
    )

    assert len(records) == 2

    for record in records:
        assert record.instrument == "lo"
        assert record.descriptor == "sci"

    assert records[0].start_date == datetime(2010, 1, 1)
    assert records[1].start_date == datetime(2010, 1, 2)
    assert records[0].version == "v002"
    assert records[1].version == "v003"
