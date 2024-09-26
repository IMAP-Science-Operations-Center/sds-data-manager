"""Tests the batch starter."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from imap_data_access import ScienceFilePath
from sqlalchemy.exc import IntegrityError

from sds_data_manager.lambda_code.SDSCode import batch_starter
from sds_data_manager.lambda_code.SDSCode.batch_starter import (
    get_downstream_dependencies,
    get_file,
    is_job_in_processing_table,
    lambda_handler,
)
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    FileCatalog,
    ProcessingJob,
)

from .conftest import POSTGRES_AVAILABLE


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


def _populate_processing_table(session):
    """Add test data to database."""
    # Add an inprogress record to the processing table
    # At the time of job kickoff, we only have these written to the table
    record = ProcessingJob(
        status=models.Status.INPROGRESS,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(record)
    session.commit()


def test_get_file(session):
    """Tests the get_file function."""
    _populate_file_catalog(session)

    record = get_file(
        session,
        instrument="ultra",
        data_level="l2",
        descriptor="sci",
        start_date="20240101",
        version="v001",
    )

    assert record.instrument == "ultra"
    assert record.data_level == "l2"
    assert record.descriptor == "sci"
    assert record.start_date == datetime(2024, 1, 1)
    assert record.version == "v001"

    # Non-existent record should return None
    record = get_file(
        session,
        instrument="ultra",
        data_level="l2",
        descriptor="sci",
        start_date="20000101",
        version="v001",
    )
    assert record is None


def test_get_downstream_dependencies(session):
    "Tests get_downstream_dependencies function."
    filename = "imap_hit_l1a_sci_20240101_v001.cdf"
    file_params = ScienceFilePath.extract_filename_components(filename)

    complete_dependents = get_downstream_dependencies(session, file_params)
    expected_complete_dependent = {
        "instrument": "hit",
        "data_level": "l1b",
        "descriptor": "sci",
        "version": "v001",
        "start_date": "20240101",
    }
    assert len(complete_dependents) == 1

    assert complete_dependents[0] == expected_complete_dependent


def test_lambda_handler(
    session,
):
    """Tests ``lambda_handler`` function."""
    _populate_file_catalog(session)

    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20240101_v001.pkts"}}'
                "}"
            }
        ]
    }

    context = {"context": "sample_context"}
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, context)
        mock_batch_client.submit_job.assert_called_once()

        # Submit a second job with the same file as input which will try to kick
        # off a duplicate job. We expect the submit_job method to not be called
        # so make sure it is still only called once from our previous iteration.
        lambda_handler(events, context)
        mock_batch_client.submit_job.assert_called_once()
    multiple_events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20240101_v001.pkts"}}'
                "}"
            },
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l1a_sci_20240101_v001.pkts"}}'
                "}"
            },
        ]
    }
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(multiple_events, context)
        mock_batch_client.submit_job.assert_called_once()


def test_is_job_in_status_table(session):
    """Test the ``is_job_in_status_table`` function."""
    _populate_processing_table(session)
    # query the processing table if this job is already in progress
    result = is_job_in_processing_table(
        session=session,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date="20100101",
        version="v001",
    )

    assert result

    result = is_job_in_processing_table(
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
    # Add some initial FAILED entries to the processing table
    # These should not be a part of the unique constraint
    for _ in range(3):
        session.add(
            ProcessingJob(
                status=models.Status.FAILED,
                instrument="lo",
                data_level="l1b",
                descriptor="de",
                start_date=datetime(2010, 1, 1),
                version="v001",
            )
        )
    session.commit()
    assert session.query(ProcessingJob).count() == 3

    record = ProcessingJob(
        status=first_status,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(record)
    session.commit()
    assert session.query(ProcessingJob).count() == 4

    duplicate = ProcessingJob(
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
    assert session.query(ProcessingJob).count() == 4

    # We can add another FAILED status without issue
    record = ProcessingJob(
        status=models.Status.FAILED,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )
    session.add(record)
    session.commit()
    assert session.query(ProcessingJob).count() == 5
