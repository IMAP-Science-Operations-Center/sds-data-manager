"""Tests the batch starter."""

import logging
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch
from urllib import parse
from urllib.error import HTTPError, URLError

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
    ScienceFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    batch_starter,
    dependency,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    IMAPDependencyFinderError,
    _get_dependencies,
    determine_job_version,
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

    dependencies = dependency.lambda_handler(event, None)
    mock_response = MagicMock()
    mock_context_manager = MagicMock()
    mock_response.read.return_value = dependencies["body"].encode("utf-8")
    mock_response.status = dependencies["statusCode"]
    # Mock the context manager and return it
    mock_context_manager.__enter__.return_value = mock_response

    return mock_context_manager


@pytest.fixture
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
                '{"object": {"key": "imap_swe_l0_raw_20240110_v001.pkts"}}'
                "}"
            }
        ]
    }
    serialized_processing_input = (
        '[{"type": "science", "files": ["imap_swe_l0_raw_20240110_v001.pkts"]}]'
    )
    context = {"context": "sample_context"}

    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
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
                    "20240110",
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
                '{"object": {"key": "imap_swe_l0_raw_20240110_v001.pkts"}}'
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
                '"imap_swe_l1b-in-flight-cal_20231231_20240102_v002.cdf"}}'
                "}"
            }
        ]
    }

    context = {"context": "sample_context"}
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, context)
        # There should be 2 different jobs submitted for one swe l1b ancillary file
        assert mock_batch_client.submit_job.call_count == 2
        # Assert_called_with only works on the last call
        # Check that the last call is what we expect with the correct dependencies

        # Even though there are two imap_swe_l1b-in-flight-cal ancillary files that
        # have valid dates, there should be only be the most recent one returned
        # as an upstream dep.
        ancillary_in = [
            AncillaryInput(
                "imap_swe_l1b-in-flight-cal_20230102_v001.cdf",
            ),
            AncillaryInput("imap_swe_esa-lut_20221231_v001.cdf"),
            AncillaryInput("imap_swe_eu-conversion_20221231_v001.cdf"),
        ]

        science_in = ScienceInput(
            "imap_swe_l1a_sci_20240102_v001.cdf",
        )
        dependencies = ProcessingInputCollection(science_in, *ancillary_in)
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
                    "20240102",
                    "--version",
                    "v002",
                    "--dependency",
                    dependencies.serialize(),
                    "--upload-to-sdc",
                ]
            },
        )


def test_lambda_handler_mag_l1c_case(session, mock_urlopen):
    """Tests ``lambda_handler` for unique mac l1c case."""
    # Mock the situation where mag l1b files trigger batch starter back to back.
    # We should expect the second job mag l1c to be submitted with a version bump and
    # both mag l1b files.
    session.add(
        ScienceFiles(
            file_path="/path/to/imap_mag_l1b_norm-mago_20240101_v001.cdf",
            instrument="mag",
            data_level="l1b",
            descriptor="norm-mago",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        )
    )
    session.commit()
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_mag_l1b_norm-mago_20240101_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}
    expected_processing_input = ProcessingInputCollection(
        ScienceInput("imap_mag_l1b_norm-mago_20240101_v001.cdf"),
    )
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, context)
        # Verify the function was called
        mock_batch_client.submit_job.assert_called_with(
            jobName="mag-l1c-norm-mago-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-mag",
            containerOverrides={
                "command": [
                    "--instrument",
                    "mag",
                    "--data-level",
                    "l1c",
                    "--descriptor",
                    "norm-mago",
                    "--start-date",
                    "20240101",
                    "--version",
                    "v001",
                    "--dependency",
                    expected_processing_input.serialize(),
                    "--upload-to-sdc",
                ]
            },
        )

        events = {
            "Records": [
                {
                    "body": '{"detail": '
                    '{"object": {"key": "imap_mag_l1b_burst-mago_20240101_v001.cdf"}}'
                    "}"
                }
            ]
        }
        session.add_all(
            [
                ScienceFiles(
                    file_path="/path/to/imap_mag_l1b_burst-mago_20240101_v001.cdf",
                    instrument="mag",
                    data_level="l1b",
                    descriptor="burst-mago",
                    start_date=datetime(2024, 1, 1),
                    version="v001",
                    extension="cdf",
                    ingestion_date=datetime.strptime(
                        "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                    ),
                ),
                ScienceFiles(
                    file_path="/path/to/imap_mag_l1b_burst-magi_20240101_v003.cdf",
                    instrument="mag",
                    data_level="l1b",
                    descriptor="burst-magi",
                    start_date=datetime(2024, 1, 1),
                    version="v003",
                    extension="cdf",
                    ingestion_date=datetime.strptime(
                        "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                    ),
                ),
            ]
        )
        session.commit()

        expected_processing_input.add(
            [ScienceInput("imap_mag_l1b_burst-mago_20240101_v001.cdf")]
        )
        lambda_handler(events, context)
        # Verify the function was called
        mock_batch_client.submit_job.assert_called_with(
            jobName="mag-l1c-norm-mago-job-3",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-mag",
            containerOverrides={
                "command": [
                    "--instrument",
                    "mag",
                    "--data-level",
                    "l1c",
                    "--descriptor",
                    "norm-mago",
                    "--start-date",
                    "20240101",
                    "--version",
                    "v002",
                    "--dependency",
                    expected_processing_input.serialize(),
                    "--upload-to-sdc",
                ]
            },
        )


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


def test_lambda_handler_no_dependencies_multiple_files(session, mock_urlopen):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _populate_file_catalog(session)
    # Test Multiple Events:
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_ultra_l2_sci_20000101_v001.cdf"}}'
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
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
        lambda_handler(events, context)
        # Verify the function was not called
        assert mock_submit.call_count == 1


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
            "Dependency API response: No records found for dependency: "
            "dep={'data_source': 'swe', 'data_type': 'l1b', 'descriptor': 'sci',"
            " 'relationship': 'HARD'}\nstart_date=datetime.datetime(2000,"
            " 1, 1, 0, 0)\nend_date=datetime.datetime(2000, 1, 1, 0, 0)"
        )
        # Verify the info statement was logged.
        assert log_str in caplog.text


def test_spice_file(session):
    """Tests ``lambda_handler`` function with spice file."""
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_1000_100_1000_100_10.spin.csv"}}'
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
        "imap_1000_100_1000_100_10.spin.csv is not implemented yet",
    ):
        lambda_handler(events, context)


def test_determine_max_version(session):
    """Test the ``determine_job_version`` function."""
    _populate_processing_table(session)
    # query the processing table and get the bumped version
    result = determine_job_version(
        session=session,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
    )
    assert result == "v002"
    # Assert that the version returned is "v001" when the job has not been processed.
    result = determine_job_version(
        session=session,
        instrument="swapi",
        data_level="l1b",
        descriptor="sci",
        start_date=datetime(2010, 1, 1),
    )
    assert result == "v001"


def test_determine_job_version_descriptor_is_all(session):
    """Test the ``determine_job_version`` function."""
    _populate_file_catalog(session)
    # With the descriptor set to "all", the function should return the max version
    # bumped of ALL of the files matching "mag" and "l1b" in the database.
    result = determine_job_version(
        session=session,
        instrument="mag",
        data_level="l1b",
        descriptor="all",
        start_date=datetime(2024, 1, 1),
    )
    assert result == "v003"


def test_determine_max_version_missing_processing_job(session):
    """Test that determine_job_version returns the correct version."""
    _populate_processing_table(session)
    _populate_file_catalog(session)
    # Test when processingJob table is not updated, the function checks
    # science_files table to get version
    result = determine_job_version(
        session=session,
        instrument="swe",
        data_level="l1a",
        descriptor="sci",
        start_date=datetime(2024, 1, 1),
    )
    assert result == "v011"


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
                dependencies='[{"type": "ancillary", "files": '
                '["imap_mag_l1b-cal_20250101_v001.cdf"]}]',
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
        {
            "data_source": "swe",
            "data_type": "l0",
            "descriptor": "raw",
            "relationship": "HARD",
        },
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
        "end_date": "20000101",
    }
    dependencies = _get_dependencies(dependency_event_msg)
    assert not dependencies
