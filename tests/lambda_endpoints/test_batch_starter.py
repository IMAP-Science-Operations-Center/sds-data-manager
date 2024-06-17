"""Tests the batch starter."""

from datetime import datetime
from unittest.mock import patch

import boto3
import pytest
from imap_data_access import ScienceFilePath
from moto import mock_batch, mock_sts
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode.batch_starter import (
    check_duplicate_job,
    get_dependency,
    lambda_handler,
    prepare_data,
    query_downstream_dependencies,
    query_instrument,
    query_upstream_dependencies,
    send_lambda_put_event,
)
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    Base,
    FileCatalog,
    StatusTracking,
)
from sds_data_manager.lambda_code.SDSCode.dependency_config import (
    all_dependents,
    downstream_dependents,
    upstream_dependents,
)


# TODO: figure out why scope of test_engine is not working properly
@pytest.fixture(scope="module")
def test_engine():
    """Create an in-memory SQLite database engine."""
    with patch.object(db, "get_engine") as mock_engine:
        engine = create_engine("sqlite:///:memory:")
        mock_engine.return_value = engine
        Base.metadata.create_all(engine)
        # When we use yield, it waits until session is complete
        # and waits for to be called whereas return exits fast.
        yield engine


# TODO: may be move this to confest.py if scope works properly
@pytest.fixture()
def populate_db(test_engine):
    """Add test data to database."""
    # Setup: Add records to the database
    with Session(db.get_engine()) as session:
        session.add_all(all_dependents)
        session.commit()
        yield session
        session.rollback()
        session.close()


@pytest.fixture()
def test_file_catalog_simulation(test_engine):
    """Return a session that has a ``FileCatalog``."""
    # Setup: Add records to the database
    test_record = [
        FileCatalog(
            file_path="/path/to/file",
            instrument="ultra",
            data_level="l2",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        FileCatalog(
            file_path="/path/to/file",
            instrument="hit",
            data_level="l0",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        FileCatalog(
            file_path="/path/to/file",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        FileCatalog(
            file_path="/path/to/file",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Adding files to test for duplicate job
        FileCatalog(
            file_path="/path/to/file",
            instrument="lo",
            data_level="l1a",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        FileCatalog(
            file_path="/path/to/file",
            instrument="lo",
            data_level="l1a",
            descriptor="spin",
            start_date=datetime(2010, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    with Session(db.get_engine()) as session:
        session.add_all(test_record)
        session.commit()

    return session


@pytest.fixture()
def populate_status_tracking_table(test_engine):
    """Add test data to database."""
    # Add an inprogress record to the status_tracking table
    # to test the check_duplicate_job function.
    # At the time of job kickoff, we only have these written to the table
    record = StatusTracking(
        status=models.Status.INPROGRESS,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    with Session(db.get_engine()) as session:
        session.add(record)
        session.commit()

    return session


@pytest.fixture()
def batch_client():
    """Yield a batch client."""
    with mock_batch():
        yield boto3.client("batch", region_name="us-west-2")


@pytest.fixture()
def sts_client():
    """Yield a STS client."""
    with mock_sts():
        yield boto3.client("sts", region_name="us-west-2")


def test_reverse_direction():
    """Test PreProcessingDependency reverse_direction method."""
    # Test that they have the same length
    assert len(downstream_dependents) == len(upstream_dependents)
    # Check that first downstream dependent is the reverse of the
    # first upstream dependent
    first_reversed_dependent = upstream_dependents[0]
    assert (
        downstream_dependents[0].relationship == first_reversed_dependent.relationship
    )
    assert (
        downstream_dependents[0].dependent_descriptor
        == first_reversed_dependent.primary_descriptor
    )
    assert (
        downstream_dependents[0].dependent_data_level
        == first_reversed_dependent.primary_data_level
    )
    assert (
        downstream_dependents[0].dependent_instrument
        == first_reversed_dependent.primary_instrument
    )
    assert (
        downstream_dependents[0].primary_descriptor
        == first_reversed_dependent.dependent_descriptor
    )
    assert (
        downstream_dependents[0].primary_data_level
        == first_reversed_dependent.dependent_data_level
    )
    assert (
        downstream_dependents[0].primary_instrument
        == first_reversed_dependent.dependent_instrument
    )

    # Check that first downstream dependent is same as the reverse of the
    # first upstream dependent
    first_reversed_dependent = upstream_dependents[0].reverse_direction()
    assert downstream_dependents[0].direction == first_reversed_dependent.direction
    assert (
        downstream_dependents[0].relationship == first_reversed_dependent.relationship
    )
    assert (
        downstream_dependents[0].dependent_descriptor
        == first_reversed_dependent.dependent_descriptor
    )
    assert (
        downstream_dependents[0].dependent_data_level
        == first_reversed_dependent.dependent_data_level
    )
    assert (
        downstream_dependents[0].dependent_instrument
        == first_reversed_dependent.dependent_instrument
    )
    assert (
        downstream_dependents[0].primary_descriptor
        == first_reversed_dependent.primary_descriptor
    )
    assert (
        downstream_dependents[0].primary_data_level
        == first_reversed_dependent.primary_data_level
    )
    assert (
        downstream_dependents[0].primary_instrument
        == first_reversed_dependent.primary_instrument
    )


def test_pre_processing_dependency(test_engine, populate_db):
    """Test pre-processing dependency."""
    # upstream dependency
    upstream_dependency = get_dependency(
        instrument="mag",
        data_level="l1a",
        descriptor="all",
        relationship="HARD",
        direction="UPSTREAM",
    )

    assert upstream_dependency[0]["instrument"] == "mag"
    assert upstream_dependency[0]["data_level"] == "l0"
    assert upstream_dependency[0]["descriptor"] == "raw"

    # downstream dependency
    downstream_dependency = get_dependency(
        instrument="mag",
        data_level="l1b",
        descriptor="norm-mago",
        relationship="HARD",
        direction="DOWNSTREAM",
    )

    assert downstream_dependency[0]["instrument"] == "mag"
    assert downstream_dependency[0]["data_level"] == "l1c"
    assert downstream_dependency[0]["descriptor"] == "norm-mago"


def test_query_instrument(test_file_catalog_simulation):
    """Tests ``query_instrument`` function."""
    upstream_dependency = {
        "instrument": "ultra",
        "data_level": "l2",
        "version": "v001",
        "descriptor": "sci",
    }

    "Tests query_instrument function."
    record = query_instrument(
        test_file_catalog_simulation,
        upstream_dependency,
        "20240101",
        "v001",
    )

    assert record.instrument == "ultra"
    assert record.data_level == "l2"
    assert record.version == "v001"
    assert record.start_date == datetime(2024, 1, 1)


def test_query_downstream_dependencies(test_file_catalog_simulation):
    "Tests query_downstream_dependencies function."
    filename = "imap_hit_l1a_sci_20240101_v001.cdf"
    file_params = ScienceFilePath.extract_filename_components(filename)
    complete_dependents = query_downstream_dependencies(
        test_file_catalog_simulation, file_params
    )

    expected_complete_dependent = {
        "instrument": "hit",
        "data_level": "l1b",
        "descriptor": "sci",
        "version": "v001",
        "start_date": "20240101",
    }

    assert complete_dependents[0] == expected_complete_dependent


def test_query_upstream_dependencies(test_file_catalog_simulation):
    """Tests ``query_upstream_dependencies`` function."""
    downstream_dependents = [
        {
            "instrument": "hit",
            "data_level": "l1a",
            "version": "v001",
            "descriptor": "sci",
            "start_date": "20240101",
        },
        {
            "instrument": "hit",
            "data_level": "l3",
            "version": "v001",
            "descriptor": "sci",
            "start_date": "20240101",
        },
    ]

    result = query_upstream_dependencies(
        test_file_catalog_simulation, downstream_dependents
    )

    assert result[0]["instrument"] == "hit"
    assert result[0]["data_level"] == "l1a"
    assert result[0]["version"] == "v001"
    expected_upstream_dependents = [
        {
            "instrument": "hit",
            "data_level": "l0",
            "descriptor": "sci",
            "start_date": "20240101",
            "version": "v001",
        }
    ]
    assert result[0]["upstream_dependencies"] == expected_upstream_dependents

    # find swe upstream dependencies
    downstream_dependents = [
        {
            "instrument": "swe",
            "data_level": "l1a",
            "version": "v001",
            "descriptor": "sci",
            "start_date": "20240101",
        }
    ]

    result = query_upstream_dependencies(
        test_file_catalog_simulation, downstream_dependents
    )

    assert len(result) == 1
    assert result[0]["instrument"] == "swe"
    assert result[0]["data_level"] == "l1a"
    assert result[0]["version"] == "v001"
    expected_upstream_dependents = [
        {
            "instrument": "swe",
            "data_level": "l0",
            "descriptor": "raw",
            "start_date": "20240101",
            "version": "v001",
        }
    ]

    assert result[0]["upstream_dependencies"] == expected_upstream_dependents

    downstream_dependents = [
        {
            "instrument": "swe",
            "data_level": "l1b",
            "version": "v001",
            "descriptor": "sci",
            "start_date": "20240101",
        }
    ]

    result = query_upstream_dependencies(
        test_file_catalog_simulation, downstream_dependents
    )

    assert len(result) == 1
    assert result[0]["instrument"] == "swe"
    assert result[0]["data_level"] == "l1b"
    assert result[0]["version"] == "v001"
    expected_upstream_dependents = [
        {
            "instrument": "swe",
            "data_level": "l1a",
            "descriptor": "sci",
            "start_date": "20240101",
            "version": "v001",
        }
    ]

    assert result[0]["upstream_dependencies"] == expected_upstream_dependents


def test_prepare_data():
    """Tests ``prepare_data`` function."""
    upstream_dependencies = [
        {
            "instrument": "hit",
            "data_level": "l0",
            "start_date": "20240101",
            "version": "v001",
        }
    ]

    filename = "imap_hit_l1a_sci_20240101_v001.cdf"
    file_params = ScienceFilePath.extract_filename_components(filename)
    prepared_data = prepare_data(
        instrument=file_params["instrument"],
        data_level=file_params["data_level"],
        start_date=file_params["start_date"],
        version=file_params["version"],
        upstream_dependencies=upstream_dependencies,
    )

    expected_prepared_data = [
        "--instrument",
        "hit",
        "--data-level",
        "l1a",
        "--start-date",
        "20240101",
        "--version",
        "v001",
        "--dependency",
        f"{upstream_dependencies}",
        "--upload-to-sdc",
    ]
    assert prepared_data == expected_prepared_data


def test_lambda_handler(
    test_file_catalog_simulation,
    batch_client,
    sts_client,
    test_engine,
):
    """Tests ``lambda_handler`` function."""
    # TODO: fix this test to use the mock batch client
    event = {"detail": {"object": {"key": "imap_hit_l1a_sci_20100101_v001.cdf"}}}
    context = {"context": "sample_context"}

    lambda_handler(event, context)


def test_check_duplicate_job(populate_status_tracking_table):
    """Test the ``check_duplicate_job`` function."""
    # query the status_tracking table if this job is already in progress
    result = check_duplicate_job(
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date="20100101",
        version="v001",
    )

    assert result

    result = check_duplicate_job(
        instrument="swapi",
        data_level="l1b",
        descriptor="sci",
        start_date="20100101",
        version="v001",
    )
    assert not result


def test_send_lambda_put_event(events_client):
    """Test the ``send_lambda_put_event`` function."""
    input_command = {
        "instrument": "mag",
        "data_level": "l1a",
        "descriptor": "lveng-hk",
        "start_date": "20231212",
        "version": "v001",
        "upstream_dependencies": [
            {
                "instrument": "swe",
                "data_level": "l0",
                "descriptor": "lveng-hk",
                "start_date": "20231212",
                "version": "v01-00",
            },
            {
                "instrument": "mag",
                "data_level": "l0",
                "descriptor": "lveng-hk",
                "start_date": "20231212",
                "version": "v001",
            },
        ],
    }

    result = send_lambda_put_event(input_command)
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
