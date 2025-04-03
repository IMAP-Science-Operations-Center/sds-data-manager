"""Tests the batch starter."""

import logging
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch
from urllib import parse
from urllib.error import HTTPError, URLError

import imap_data_access.processing_input
import pytest
from imap_data_access.processing_input import (
    AncillaryInput,
    ProcessingInputCollection,
    ScienceInput,
)
from sqlalchemy.exc import IntegrityError

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    ProcessingJob,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    batch_starter,
    dependency,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    IMAPDependencyFinderError,
    _get_dependencies,
    is_job_in_processing_table,
    lambda_handler,
)

from .conftest import (
    POSTGRES_AVAILABLE,
    _populate_file_catalog,
    create_dependency_api_event,
)


def urlopen_side_effect(url):
    """Create a list of dependencies based on the api request url.

    Parameters
    ----------
    url : str
       The request url

    Returns
    -------
    unittest.mock.MagicMock
       A mock context manager returning a HTTP response with the expected dependencies.
    """
    parsed_url = parse.urlparse(url)
    params = parse.parse_qs(parsed_url.query)
    event = create_dependency_api_event(
        params.get("data_source")[0],
        params.get("data_type")[0],
        params.get("descriptor")[0],
        params.get("dependency_type")[0],
        params.get("relationship")[0],
        params.get("start_date", [None])[0],
        params.get("end_date", [None])[0],
        params.get("version", [None])[0],
        params.get("trigger_type", [None])[0],
    )

    dependencies = dependency.lambda_handler(event, None)["body"]
    mock_response = MagicMock()
    mock_context_manager = MagicMock()
    mock_response.read.return_value = dependencies.encode("utf-8")
    # Mock the context manager and return it
    mock_context_manager.__enter__.return_value = mock_response

    return mock_context_manager


@pytest.fixture()
def mock_urlopen():
    """Mock urlopen to return a list of dependency dictionaries.

    Yields
    ------
    mock_urlopen : unittest.mock.MagicMock
        Mock object for ``urlopen``
    """
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urlopen_side_effect
        yield mock_urlopen


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


def test_lambda_handler(
    session,
    mock_urlopen: unittest.mock.MagicMock,
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
    serialized_processing_input = (
        '[{"type": "science", "files": ["imap_swe_l0_raw_20240101_v001.pkts"]}]'
    )
    context = {"context": "sample_context"}
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, context)
        mock_batch_client.submit_job.assert_called_once()

        # Submit a second job with the same file as input which will try to kick
        # off a duplicate job. We expect the submit_job method to not be called
        # so make sure it is still only called once from our previous iteration.
        lambda_handler(events, context)
        mock_batch_client.submit_job.assert_called_once()
        mock_batch_client.submit_job.assert_called_with(
            jobName="swe-l1a-sci-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-swe",
            containerOverrides={
                "command": [
                    "--instrument",
                    "swe",
                    "--data-level",
                    "l1a",
                    "--descriptor",
                    "sci",
                    "--start-date",
                    "20240101",
                    "--version",
                    "v001",
                    "--dependency",
                    serialized_processing_input,
                    "--upload-to-sdc",
                ]
            },
        )


def test_lambda_handler_multiple_events(session, mock_urlopen):
    """Tests ``lambda_handler`` function with multiple events."""
    # Test Multiple Events:
    _populate_file_catalog(session)
    multiple_events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20240101_v001.pkts"}}'
                "}"
            },
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l1a_sci_20240101_v001.cdf"}}'
                "}"
            },
        ]
    }
    context = {"context": "sample_context"}
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(multiple_events, context)
        assert mock_batch_client.submit_job.call_count == 2


def test_lambda_handler_ancillary_event(
    session,
    mock_urlopen: unittest.mock.MagicMock,
):
    """Tests ``lambda_handler`` function when triggerd by an ancillary file."""
    _populate_file_catalog(session)
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": '
                '"imap_swe_l1b-in-flight-cal_20240101_v001.cdf"}}'
                "}"
            }
        ]
    }

    context = {"context": "sample_context"}
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, context)
        # There should be two different jobs submitted for one swe l1b ancillary file
        assert mock_batch_client.submit_job.call_count == 2
        # Assert_called_with only works on the last call
        # Check that the last call is what we expect with the corrected
        ancillary_in = AncillaryInput(
            "imap_swe_l1b-in-flight-cal_20230101_v001.cdf",
            "imap_swe_l1b-in-flight-cal_20231231_20240102_v002.cdf",
        )
        science_in = ScienceInput(
            "imap_swe_l1a_sci_20240103_v001.cdf",
        )
        dependencies = ProcessingInputCollection(science_in, ancillary_in)
        mock_batch_client.submit_job.assert_called_with(
            jobName="swe-l1b-sci-job-2",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-swe",
            containerOverrides={
                "command": [
                    "--instrument",
                    "swe",
                    "--data-level",
                    "l1b",
                    "--descriptor",
                    "sci",
                    "--start-date",
                    "20240103",
                    "--version",
                    "v001",
                    "--dependency",
                    dependencies.serialize(),
                    "--upload-to-sdc",
                ]
            },
        )
        # Submit a second job with the same file as input which will try to kick
        # off a duplicate job. We expect the submit_job method to not be called
        # so make sure it is still only called two times from our previous iteration.
        mock_batch_client.submit_job.call_count = 0
        lambda_handler(events, context)
        assert mock_batch_client.submit_job.call_count == 0


def test_lambda_handler_no_dependencies(session, mock_urlopen):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _populate_file_catalog(session)
    # Test Multiple Events:
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_ultra_l2_sci_20000101_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
        lambda_handler(events, context)
        # Verify the function was not called
        assert mock_submit.call_count == 0


def test_lambda_handler_missing_upstream_dependency(session, mock_urlopen, caplog):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _populate_file_catalog(session)
    # Test Multiple Events:
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l1b_sci_20000101_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}
    with caplog.at_level(logging.DEBUG):
        lambda_handler(events, context)
        log_str = (
            "Upstream dependency not found for: {'data_source': "
            "'swe', 'data_type': 'l2', 'descriptor': 'sci', 'dependency_type': "
            "'UPSTREAM', 'relationship': 'HARD', 'start_date': '20000101', "
            "'version': 'v001', 'trigger_type': 'l1b'}"
        )
        # Verify the info statement was logged.
        assert log_str in caplog.text


def test_spice_file():
    """Tests ``lambda_handler`` function with spice file."""
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_yyyy_doy_yyyy_doy.spin.csv"}}'
                "}"
            }
        ]
    }

    context = {"context": "sample_context"}

    # Test that value error is raised for SPICE file right now.
    # TODO: undo this and add correct tests when it's implemented.
    with pytest.raises(
        ValueError,
        match="Batch starter handling for spice file: "
        "imap_yyyy_doy_yyyy_doy.spin.csv is not implemented yet",
    ):
        lambda_handler(events, context)


def test_is_job_in_status_table(session):
    """Test the ``is_job_in_status_table`` function."""
    _populate_processing_table(session)
    # query the processing table if this job is already in progress
    result = is_job_in_processing_table(
        session=session,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
    )

    assert result

    result = is_job_in_processing_table(
        session=session,
        instrument="swapi",
        data_level="l1b",
        descriptor="sci",
        start_date=datetime(2010, 1, 1),
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


def test_api_request_error(mock_urlopen: unittest.mock.MagicMock):
    """Test that invalid URLs raise an appropriate HTTPError or URLError.

    Parameters
    ----------
    mock_urlopen : unittest.mock.MagicMock
        Mock object for ``urlopen``
    """
    dependency_event_msg = {
        "data_source": "swe",
        "data_type": "l1a",
        "descriptor": "sci",
        "dependency_type": "UPSTREAM",
        "relationship": "HARD",
    }
    # Set up the mock to raise an HTTPError
    mock_urlopen.side_effect = HTTPError(
        url="http://example.com", code=404, msg="Not Found", hdrs={}, fp=BytesIO()
    )
    with pytest.raises(IMAPDependencyFinderError, match="HTTP Error"):
        _get_dependencies(dependency_event_msg)

    # Set up the mock to raise a URLError
    mock_urlopen.side_effect = URLError(reason="Not Found")
    with pytest.raises(IMAPDependencyFinderError, match="URL Error"):
        _get_dependencies(dependency_event_msg)


def test_api_request_success(mock_urlopen: unittest.mock.MagicMock):
    """Test that _get_dependencies() returns the expected dependency result.

    Parameters
    ----------
    mock_urlopen : unittest.mock.MagicMock
        Mock object for ``urlopen``
    """
    dependency_event_msg = {
        "data_source": "swe",
        "data_type": "l1a",
        "descriptor": "sci",
        "dependency_type": "UPSTREAM",
        "relationship": "HARD",
    }
    dependencies = _get_dependencies(dependency_event_msg)
    assert dependencies == [
        {"data_source": "swe", "data_type": "l0", "descriptor": "raw"}
    ]


def test_api_request_success_empty(session, mock_urlopen: unittest.mock.MagicMock):
    """Test that _get_dependencies() returns the expected dependency result.

    Parameters
    ----------
    session : orm session
        Mock database session.
    mock_urlopen : unittest.mock.MagicMock
        Mock object for ``urlopen``
    """
    dependency_event_msg = {
        "data_source": "swe",
        "data_type": "l1a",
        "descriptor": "sci",
        "dependency_type": "UPSTREAM",
        "relationship": "HARD",
        "start_date": "20000101",
        "version": "v001",
        "trigger_type": "swe",
    }
    dependencies = _get_dependencies(dependency_event_msg)
    assert dependencies == imap_data_access.processing_input.ProcessingInputCollection()
