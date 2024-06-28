"""Tests the batch starter."""

import copy
from datetime import datetime

import pytest
from imap_data_access import ScienceFilePath
from sqlalchemy.exc import IntegrityError

from sds_data_manager.lambda_code.SDSCode.batch_starter import (
    get_dependency,
    is_job_in_status_table,
    lambda_handler,
    query_downstream_dependencies,
    query_instrument,
    query_upstream_dependencies,
)
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    FileCatalog,
    StatusTracking,
)
from sds_data_manager.lambda_code.SDSCode.dependency_config import (
    all_dependents,
    downstream_dependents,
    upstream_dependents,
)

from .conftest import POSTGRES_AVAILABLE


def _populate_dependency_table(session):
    """Add test data to database."""
    # We need to deepcopy these, otherwise there is an old
    # reference hanging around that prevents the continual
    # addition of these records to the database for each new
    # test.
    session.add_all(copy.deepcopy(all_dependents))
    session.commit()


def _populate_file_catalog(session):
    """Add records to the file catalog."""
    # Setup: Add records to the database
    test_records = [
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
    session.add_all(test_records)
    session.commit()


def _populate_status_tracking_table(session):
    """Add test data to database."""
    # Add an inprogress record to the status_tracking table
    # to test the is_job_in_status_table function.
    # At the time of job kickoff, we only have these written to the table
    record = StatusTracking(
        status=models.Status.INPROGRESS,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(record)
    session.commit()


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


def test_pre_processing_dependency(session):
    """Test pre-processing dependency."""
    _populate_dependency_table(session)
    # upstream dependency
    upstream_dependency = get_dependency(
        session=session,
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
        session=session,
        instrument="mag",
        data_level="l1b",
        descriptor="norm-mago",
        relationship="HARD",
        direction="DOWNSTREAM",
    )

    assert downstream_dependency[0]["instrument"] == "mag"
    assert downstream_dependency[0]["data_level"] == "l1c"
    assert downstream_dependency[0]["descriptor"] == "norm-mago"


def test_query_instrument(session):
    """Tests ``query_instrument`` function."""
    _populate_file_catalog(session)
    upstream_dependency = {
        "instrument": "ultra",
        "data_level": "l2",
        "version": "v001",
        "descriptor": "sci",
    }

    "Tests query_instrument function."
    record = query_instrument(
        session,
        upstream_dependency,
        "20240101",
        "v001",
    )

    assert record.instrument == "ultra"
    assert record.data_level == "l2"
    assert record.version == "v001"
    assert record.start_date == datetime(2024, 1, 1)


def test_query_downstream_dependencies(session):
    "Tests query_downstream_dependencies function."
    _populate_dependency_table(session)
    filename = "imap_hit_l1a_sci_20240101_v001.cdf"
    file_params = ScienceFilePath.extract_filename_components(filename)

    complete_dependents = query_downstream_dependencies(session, file_params)
    expected_complete_dependent = {
        "instrument": "hit",
        "data_level": "l1b",
        "descriptor": "sci",
        "version": "v001",
        "start_date": "20240101",
    }

    assert complete_dependents[0] == expected_complete_dependent


def test_query_upstream_dependencies(session):
    """Tests ``query_upstream_dependencies`` function."""
    _populate_dependency_table(session)
    _populate_file_catalog(session)
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

    result = query_upstream_dependencies(session, downstream_dependents)
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

    result = query_upstream_dependencies(session, downstream_dependents)

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

    result = query_upstream_dependencies(session, downstream_dependents)

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


def test_lambda_handler(
    session,
    batch_client,
):
    """Tests ``lambda_handler`` function."""
    event = {"detail": {"object": {"key": "imap_hit_l1a_sci_20100101_v001.cdf"}}}
    context = {"context": "sample_context"}
    lambda_handler(event, context)


def test_is_job_in_status_table(session):
    """Test the ``is_job_in_status_table`` function."""
    _populate_status_tracking_table(session)
    # query the status_tracking table if this job is already in progress
    result = is_job_in_status_table(
        session=session,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date="20100101",
        version="v001",
    )

    assert result

    result = is_job_in_status_table(
        session=session,
        instrument="swapi",
        data_level="l1b",
        descriptor="sci",
        start_date="20100101",
        version="v001",
    )
    assert not result


@pytest.mark.skipif(
    not POSTGRES_AVAILABLE, reason="Only postgres supports partial unique indexes."
)
# Loop over all combinations of status attempts that should fail
@pytest.mark.parametrize(
    "first_status", [models.Status.INPROGRESS, models.Status.SUCCEEDED]
)
@pytest.mark.parametrize(
    "second_status", [models.Status.INPROGRESS, models.Status.SUCCEEDED]
)
def test_duplicate_job(session, first_status, second_status):
    """Multiple jobs in progress should raise an IntegrityError."""
    # Add some initial FAILED entries to the status_tracking table
    # These should not be a part of the unique constraint
    for _ in range(3):
        session.add(
            StatusTracking(
                status=models.Status.FAILED,
                instrument="lo",
                data_level="l1b",
                descriptor="de",
                start_date=datetime(2010, 1, 1),
                version="v001",
            )
        )
    session.commit()
    assert session.query(StatusTracking).count() == 3

    record = StatusTracking(
        status=first_status,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(record)
    session.commit()
    assert session.query(StatusTracking).count() == 4

    duplicate = StatusTracking(
        status=second_status,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    # After an error, we need to rollback the commit
    session.rollback()

    # Now we should still only have 4 items in the table
    assert session.query(StatusTracking).count() == 4

    # We can add another FAILED status without issue
    record = StatusTracking(
        status=models.Status.FAILED,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(record)
    session.commit()
    assert session.query(StatusTracking).count() == 5
