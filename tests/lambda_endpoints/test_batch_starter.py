"""Tests the batch starter."""

import datetime as dt
import json
import logging
import os
import pathlib
from datetime import datetime
from os.path import basename
from unittest.mock import Mock, call, patch

import imap_data_access
import pytest
from imap_data_access import SpinInput
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    ScienceInput,
    SPICEInput,
)
from sqlalchemy.exc import IntegrityError

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    AncillaryFiles,
    PointingTable,
    ProcessingJob,
    RepointFiles,
    ScienceFiles,
    SPICEFiles,
    SpinFiles,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    batch_starter,
    dependency,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    CadenceDays,
    determine_date_range,
    determine_job_version,
    lambda_handler,
    upload_dependency_file,
)

from .conftest import (
    POSTGRES_AVAILABLE,
    _static_spice_files,
)


@pytest.fixture
def auth_event():
    """Create an event with authentication information."""

    def _auth_event(event_dict=None):
        # Start with a base event that includes authentication path info
        auth_base = {
            "version": "2.0",
            "routeKey": "POST /api-key/reprocess",
            "rawPath": "/api-key/reprocess",
        }

        # If event_dict is provided, merge it with auth_base
        if event_dict:
            # For queryStringParameters, we want to copy them specifically
            if "queryStringParameters" in event_dict:
                auth_base["queryStringParameters"] = event_dict["queryStringParameters"]

            # For any other keys, just copy them over
            for key, value in event_dict.items():
                if key != "queryStringParameters":
                    auth_base[key] = value

        return auth_base

    return _auth_event


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


def test_lambda_handler(session, s3_client):
    """Tests that SWE L0 file ingestion kicks off job."""
    _static_spice_files(session)
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20240101_v001.pkts"}}'
                "}",
                "receiptHandle": "testingtesting123",
                "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:"
                "testing-queue-url.fifo",
            }
        ]
    }

    # Other records needed for this test
    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20240101_v001.pkts",
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
    ]
    session.add_all(records)
    session.commit()

    processing_input = ProcessingInputCollection(
        SPICEInput("naif0012.tls", "imap_sclk_0000.tsc"),
        ScienceInput("imap_swe_l0_raw_20240101_v001.pkts"),
    )
    context = {"context": "sample_context"}
    mock_sqs_client = Mock()
    mock_sqs_client.delete_message.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200}
    }

    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter.SQS_CLIENT",
            mock_sqs_client,
        ),
    ):
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
                    "imap_swe_l1a_sci-c685cc19_20240101_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )
    mock_sqs_client.delete_message.assert_called_once_with(
        QueueUrl="https://sqs.us-west-2.amazonaws.com/123456789012/testing-queue-url.fifo",
        ReceiptHandle="testingtesting123",
    )  # Verify the function was called with the correct upstream dependencies

    with (
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter.SQS_CLIENT",
            mock_sqs_client,
        ),
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
    ):
        lambda_handler(events, context)
        mock_submit.assert_called_with(
            session,
            {"data_source": "swe", "data_type": "l1a", "descriptor": "sci"},
            "20240101",
            "v001",
            processing_input.serialize(),
            repoint=None,
        )


def test_different_queues(session, s3_client):
    """Tests events from multiple queues."""
    _static_spice_files(session)

    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20240110_v001.pkts"}}'
                "}",
                "receiptHandle": "testingtesting123",
                "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:"
                "testing-queue-url.fifo",
            },
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20240110_v001.pkts"}}'
                "}",
                "receiptHandle": "testingtesting222",
                "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:"
                "delay-queue-url.fifo",
            },
        ]
    }
    context = {"context": "sample_context"}
    mock_sqs_client = Mock()
    mock_sqs_client.delete_message.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200}
    }

    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()),
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter.SQS_CLIENT",
            mock_sqs_client,
        ),
    ):
        lambda_handler(events, context)
        # Both events need to be removed from the correct queue
        mock_sqs_client.delete_message.assert_has_calls(
            [
                call(
                    QueueUrl="https://sqs.us-west-2.amazonaws.com/123456789012/testing-queue-url.fifo",
                    ReceiptHandle="testingtesting123",
                ),
                call(
                    QueueUrl="https://sqs.us-west-2.amazonaws.com/123456789012/delay-queue-url.fifo",
                    ReceiptHandle="testingtesting222",
                ),
            ]
        )


def test_lambda_handler_multiple_events(session, s3_client):
    """Tests ``lambda_handler`` function with multiple events."""
    _static_spice_files(session)
    # Other db records needed for this test
    records = [
        # record needed for L0 file ingestion
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20250110_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2025, 1, 10),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # record needed for L1A file ingestion
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20251201_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2025, 12, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20250102_v002.csv",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2025, 1, 2),
            version="v002",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_esa-lut_20250101_v001.csv",
            instrument="swe",
            descriptor="esa-lut",
            start_date=datetime(2025, 1, 1),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_eu-conversion_20250101_v001.csv",
            instrument="swe",
            descriptor="eu-conversion",
            start_date=datetime(2025, 1, 1),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    multiple_events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l0_raw_20250110_v001.pkts"}}'
                "}"
            },
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l1a_sci_20251201_v001.cdf"}}'
                "}"
            },
        ]
    }
    context = {"context": "sample_context"}
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(multiple_events, context)
        assert mock_batch_client.submit_job.call_count == 2


def test_lambda_handler_spice_event(session):
    """Tests ``lambda_handler`` function when triggerd by an spice file."""
    _static_spice_files(session)
    # Test that the correct dependencies are gathered when a spice ingest
    # triggers the lambda.
    # Swe l2 needs l1b and spin files.
    # Add db records to satisfy dependencies.
    records = [
        # Add two science files to ensure there are two jobs submitted
        ScienceFiles(
            file_path="/path/to/imap_swe_l1b_sci_20250429_v001.cdf",
            instrument="swe",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2025, 4, 29),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_swe_l1b_sci_20250430_v001.cdf",
            instrument="swe",
            data_level="l1b",
            descriptor="sci",
            start_date=datetime(2025, 4, 30),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        SpinFiles(
            file_path="/imap/spice/spin/imap_2025_119_2025_121_01.spin.csv",
            start_date=datetime(2025, 4, 29),
            end_date=datetime(2025, 4, 30),
            version="01",
            ingestion_date=datetime.now(),
        ),
    ]
    session.add_all(records)
    session.commit()

    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": '
                '"imap_2025_119_2025_121_01.spin.csv"}}'
                "}"
            }
        ]
    }

    context = {"context": "sample_context"}
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # There should be 2 different jobs submitted for the two swe l1b files
        assert mock_batch_client.submit_job.call_count == 2
        # Assert_called_with only works on the last call
        # Check that the last call is what we expect with the correct dependencies

        mock_batch_client.submit_job.assert_called_with(
            jobName="swe-l2-sci-job-2",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-swe",
            containerOverrides={
                "command": [
                    "--instrument",
                    "swe",
                    "--data-level",
                    "l2",
                    "--descriptor",
                    "sci",
                    "--start-date",
                    "20250430",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_swe_l2_sci-d3646148_20250430_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )
    # Assert that the try_to_submit_job function was called with the correct
    # upstream dependencies
    # There should be two calls to try_to_submit_job, one for each swe l1b file
    # Each job should have the same spin file dependency, but different
    # science file dependencies.
    # We check the last call here.
    expected_dependencies = ProcessingInputCollection(
        SpinInput("imap_2025_119_2025_121_01.spin.csv"),
        ScienceInput("imap_swe_l1b_sci_20250430_v001.cdf"),
    )
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        mock_submit.assert_called_with(
            session,
            {"data_source": "swe", "data_type": "l2", "descriptor": "sci"},
            "20250430",
            "v001",
            expected_dependencies.serialize(),
            repoint=None,
        )


def test_lambda_handler_ancillary_event(session):
    """Tests ``lambda_handler`` function when triggerd by an ancillary file."""
    _static_spice_files(session)
    # Other db records needed to proccess l1a to l1b when ancillary file is ingested
    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20260303_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2026, 3, 3),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20260303_v001.csv",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2026, 3, 3),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_esa-lut_20260303_v001.csv",
            instrument="swe",
            descriptor="esa-lut",
            start_date=datetime(2026, 3, 3),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_eu-conversion_20260303_v001.csv",
            instrument="swe",
            descriptor="eu-conversion",
            start_date=datetime(2026, 3, 3),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": '
                '"imap_swe_l1b-in-flight-cal_20260101_20260401_v002.cdf"}}'
                "}"
            }
        ]
    }

    context = {"context": "sample_context"}
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # There should be 2 different jobs submitted for one swe l1b ancillary file
        assert mock_batch_client.submit_job.call_count == 1
        # Assert_called_with only works on the last call
        # Check that the last call is what we expect with the correct dependencies

        # Even though there are two imap_swe_l1b-in-flight-cal ancillary files that
        # have valid dates, there should be only be the most recent one returned
        # as an upstream dep.
        inputs = [
            {"type": "spice", "files": ["naif0012.tls", "imap_sclk_0000.tsc"]},
            {"type": "science", "files": ["imap_swe_l1a_sci_20260303_v001.cdf"]},
            {
                "type": "ancillary",
                "files": ["imap_swe_l1b-in-flight-cal_20260303_v001.csv"],
            },
            {"type": "ancillary", "files": ["imap_swe_esa-lut_20260303_v001.csv"]},
            {
                "type": "ancillary",
                "files": ["imap_swe_eu-conversion_20260303_v001.csv"],
            },
        ]
        mock_batch_client.submit_job.assert_called_with(
            jobName="swe-l1b-sci-job-1",
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
                    "20260303",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_swe_l1b_sci-6a22366c_20260303_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )
    # Assert that the try_to_submit_job function was called with the correct
    # upstream dependencies
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        mock_submit.assert_called_with(
            session,
            {"data_source": "swe", "data_type": "l1b", "descriptor": "sci"},
            "20260303",
            "v001",
            json.dumps(inputs),
            repoint=None,
        )


def test_lambda_handler_no_dependencies(session):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _static_spice_files(session)
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
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Verify the function was not called
        assert mock_submit.call_count == 0


def test_lambda_handler_no_dependencies_multiple_files(session):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _static_spice_files(session)
    # Other db records needed to kick of swe l1a job.
    # Ultra is not kicked off because there are no dependencies.
    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20250101_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2025, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
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
                '{"object": {"key": "imap_swe_l0_raw_20250101_v001.pkts"}}'
                "}"
            },
        ]
    }
    context = {"context": "sample_context"}
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Verify the function was not called
        assert mock_submit.call_count == 1


def test_lambda_handler_missing_upstream_dependency(session, caplog):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _static_spice_files(session)
    # Test Multiple Events:
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_swe_l1b_sci_20000102_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Look for the log message in the log output, which should contain
        # the dependency information
        assert "No spin files found for" in caplog.text
        assert (
            "Skipping job submission for {'data_source': 'swe', "
            "'data_type': 'l2', 'descriptor': 'sci'} because of a "
            "missing upstream dependency." in caplog.text
        )


def test_lambda_handler_missing_dependency_for_start_date(session, caplog):
    """Tests ``lambda_handler`` function for a specific case."""
    _static_spice_files(session)
    # This test covers a rare scenario: when a new ancillary file is uploaded, the
    # dependency handler might find jobs to run where the uploaded file is valid for the
    # job's start_date, but another required ancillary file is not. The test ensures
    # that in these cases, the job is skipped.
    records = [
        ScienceFiles(
            file_path="/path/to/imap_mag_l1c_norm-mago_20250418_v004.cdf",
            instrument="mag",
            data_level="l1c",
            descriptor="norm-mago",
            start_date=datetime(2025, 4, 18),
            version="v004",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_mag_l2-calibration_20250117_v001.cdf",
            instrument="mag",
            descriptor="l2-calibration",
            start_date=datetime(2025, 1, 17),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_mag_l2-norm-offsets_20250421_v001.cdf",
            instrument="mag",
            descriptor="l2-norm-offsets",
            start_date=datetime(2025, 4, 21),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # add pointing_attitude
        SPICEFiles(
            file_path="path/to/imap_dps_2024_001_2026_001_01.ah.bc",
            file_name="imap_dps_2024_001_2026_001_01.ah.bc",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_dps_2024_001_2026_.ah.bc",
            kernel_type="pointing_attitude",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=1,
        ),
    ]
    session.add_all(records)
    session.commit()

    multiple_events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_mag_l2-calibration_20250117_v001.cdf"}}'
                "}"
            },
        ]
    }
    caplog.set_level("INFO")
    context = {"context": "sample_context"}
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(multiple_events, context)
        assert mock_batch_client.submit_job.call_count == 0
    # Check that the expected message was logged.
    expected_log = (
        "Skipping job submission for {'data_source': 'mag', 'data_type': "
        "'l2', 'descriptor': 'norm-srf'} with start_date: 20250418 because"
        " of a missing upstream dependency."
    )
    assert expected_log in caplog.text


###### BULK REPROCESSING TESTS #######
def test_bulk_reprocessing_data_level(session, caplog, auth_event):
    """Tests bulk reprocessing for a data level."""
    _static_spice_files(session)
    records = [
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20220101_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2022, 1, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        )
    ]
    session.add_all(records)
    session.commit()
    # Test with an invalid event first. If data_level is provided, then instrument and
    # descriptor are required.
    query_params = {
        "reprocessing": "True",
        "start_date": "20220101",
        "end_date": "20220301",
        "data_level": "l1a",
        "descriptor": "sci",
    }
    # Create an authenticated event
    events = auth_event({"queryStringParameters": query_params})
    context = {"context": "sample_context"}
    with pytest.raises(ValueError, match="instrument and descriptor are required"):
        lambda_handler(events, context)
    # Add instrument and try again
    events["queryStringParameters"]["instrument"] = "swe"
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
    # There should be 4 different jobs submitted for swe l1b sci because there are 4
    # upstream swe l1a sci files with start dates in the reprocessing range.

    assert mock_submit.call_count == 1


def test_bulk_reprocessing_all(session, caplog, auth_event):
    """Tests ``lambda_handler`` when there is bulk reprocessing for all instruments."""
    _static_spice_files(session)
    # db records needed for this reprocessing test
    records = [
        # record needed for l1a reprocessing
        ScienceFiles(
            file_path="/path/to/imap_swe_l0_raw_20250110_v001.pkts",
            instrument="swe",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2025, 1, 10),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # record needed for l1b reprocessing
        ScienceFiles(
            file_path="/path/to/imap_swe_l1a_sci_20251201_v001.cdf",
            instrument="swe",
            data_level="l1a",
            descriptor="sci",
            start_date=datetime(2025, 12, 1),
            version="v001",
            extension="pkts",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_l1b-in-flight-cal_20250102_v002.csv",
            instrument="swe",
            descriptor="l1b-in-flight-cal",
            start_date=datetime(2025, 1, 2),
            version="v002",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_esa-lut_20250101_v001.csv",
            instrument="swe",
            descriptor="esa-lut",
            start_date=datetime(2025, 1, 1),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_swe_eu-conversion_20250101_v001.csv",
            instrument="swe",
            descriptor="eu-conversion",
            start_date=datetime(2025, 1, 1),
            version="v001",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()

    # leave instrument, data_level and descriptor blank
    query_params = {
        "reprocessing": "True",
        "start_date": "20230101",
        "end_date": "20260101",
    }
    # Create an authenticated event
    events = auth_event({"queryStringParameters": query_params})
    context = {"context": "sample_context"}
    # Add instrument and try again
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
    # It should kickoff two jobs for swe l1a and l1b.
    # TODO: find out why it only kicks off certain levels
    assert mock_submit.call_count == 1


def test_bulk_reprocessing_all_swe(session, caplog):
    """Tests ``lambda_handler`` when there is bulk reprocessing for all instruments."""
    # leave instrument, data_level and descriptor blank
    _static_spice_files(session)
    events = {
        "queryStringParameters": {
            "reprocessing": "True",
            "start_date": "20230101",
            "end_date": "20260101",
            "data_level": "l1a",
            "descriptor": "sci",
            "instrument": "swe",
        }
    }
    context = {"context": "sample_context"}
    # Add instrument and try again
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
    # There should be one job submitted for swe
    assert mock_submit.call_count == 0


def test_lambda_handler_mag_l1c_case(session):
    """Tests ``lambda_handler` for unique mac l1c case."""
    # Mock the situation where mag l1b files trigger batch starter back to back.
    # We should expect the second job mag l1c to be submitted with a version bump and
    # both mag l1b files.
    _static_spice_files(session)
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
        SPICEInput("naif0012.tls", "imap_sclk_0000.tsc"),
        ScienceInput("imap_mag_l1b_norm-mago_20240101_v001.cdf"),
    )
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
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
                    "imap_mag_l1c_norm-mago-34e68524_20240101_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
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
            jobName="mag-l1c-norm-mago-job-2",
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
                    "imap_mag_l1c_norm-mago-78046f35_20240101_v002.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )
    # Assert that the try_to_submit_job function was called with the correct
    # upstream dependencies
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        mock_submit.assert_called_with(
            session,
            {"data_source": "mag", "data_type": "l1c", "descriptor": "norm-mago"},
            "20240101",
            "v002",
            expected_processing_input.serialize(),
            repoint=None,
        )


def test_lambda_handler_duplicate_mag_l1c_job(session, caplog):
    """Tests ``lambda_handler` skips processing for a duplicate job."""
    # Mock the situation where mag l1b files trigger batch starter back to back but
    # with the same exact dependencies.
    # We should expect the duplicate job to be skipped.
    _static_spice_files(session)
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
                file_path="/path/to/imap_mag_l1b_norm-mago_20240101_v003.cdf",
                instrument="mag",
                data_level="l1b",
                descriptor="norm-mago",
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
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_mag_l1b_burst-mago_20240101_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}

    # Mock the database constraint that prevents duplicate ProcessingJob records
    # The real database uses PostgreSQL constraints, but tests use SQLite which doesn't
    # support them
    original_add = session.add

    def mock_add(obj):
        if isinstance(obj, ProcessingJob):
            # Check for duplicate based on unique constraint fields in the actual
            # session
            existing_jobs = (
                session.query(ProcessingJob)
                .filter(
                    ProcessingJob.instrument == obj.instrument,
                    ProcessingJob.data_level == obj.data_level,
                    ProcessingJob.descriptor == obj.descriptor,
                    ProcessingJob.start_date == obj.start_date,
                    ProcessingJob.version == obj.version,
                    ProcessingJob.repointing == obj.repointing,
                    ProcessingJob.status.in_(["INPROGRESS", "SUCCEEDED"]),
                )
                .all()
            )

            if existing_jobs:
                raise IntegrityError(
                    "duplicate key value violates unique constraint", None, None
                )
        return original_add(obj)

    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
        patch.object(session, "add", side_effect=mock_add),
    ):
        lambda_handler(events, context)
        # Verify the function was called
        mock_batch_client.submit_job.assert_called_once()

        events = {
            "Records": [
                {
                    "body": '{"detail": '
                    '{"object": {"key": "imap_mag_l1b_norm-mago_20240101_v003.cdf"}}'
                    "}"
                }
            ]
        }
        # Reset call count
        mock_batch_client.submit_job.call_count = 0
        lambda_handler(events, context)
        # Verify the function not called
        assert mock_batch_client.submit_job.call_count == 0

        assert ("Job already completed or in progress") in caplog.text


### TEST CADENCE EVENT
def test_def_cadence_map_event(setup_s3, session, tmp_path):
    """Test that a cadence event kicks off the right processing job."""
    _static_spice_files(session)
    # Add 10 months of ultra l1c "45sensor" pset files to the database
    session.add_all(
        [
            ScienceFiles(
                file_path=f"/path/to/imap_ultra_l1c_45sensor-{pset_type}pset_2025{month:02}01_v001.cdf",
                instrument="ultra",
                data_level="l1c",
                descriptor=f"45sensor-{pset_type}pset",
                start_date=datetime(2025, month, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
            for pset_type, month in zip(
                ["spacecraft"] * 5 + ["helio"] * 5, range(1, 10)
            )
        ]
    )
    # Add 10 months of ultra l1c "90sensor" pset files to the database
    session.add_all(
        [
            ScienceFiles(
                file_path=f"/path/to/imap_ultra_l1c_90sensor-{pset_type}pset_2025{month:02}01_v001.cdf",
                instrument="ultra",
                data_level="l1c",
                descriptor=f"90sensor-{pset_type}pset",
                start_date=datetime(2025, month, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
            for pset_type, month in zip(
                ["spacecraft"] * 5 + ["helio"] * 5, range(1, 10)
            )
        ]
    )
    session.add_all(
        [
            # Add pointing attitude
            SPICEFiles(
                file_path="path/to/imap_dps_2024_001_2024_001_01.ah.bc",
                file_name="imap_dps_2024_001_2024_001_01.ah.bc",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_root="imap_dps_2024_001_2024_.ah.bc",
                kernel_type="pointing_attitude",
                min_date_j2000=0,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[0, 4575787269.183866]],
                min_date_datetime=datetime.strptime(
                    "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                max_date_datetime=datetime.strptime(
                    "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
                min_date_sclk="1/0000000000:00000",
                max_date_sclk="1/4285909749:39444",
                file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
                sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
                lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
                version=1,
            ),
        ]
    )
    session.commit()

    cadence_event = {
        "cadence": "3mo",
    }
    context = {"context": "sample_context"}
    with (
        patch.object(batch_starter, "BATCH_CLIENT") as mock_batch_client,
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter"
            ".cadence_to_datetime_range"
        ) as dt_mock,
        patch("imap_data_access.config", {"DATA_DIR": tmp_path}),
    ):
        dt_mock.return_value = ("20250301", "20250601")
        lambda_handler(cadence_event, context)
        # Verify the function was called 12 times. There are currently 12 l2 map jobs
        # with the cadence of 3 months.
        assert mock_batch_client.submit_job.call_count == 12
        # Assert that the function was called with the cadence json file path
        mock_batch_client.submit_job.assert_called_with(
            jobName="ultra-l2-u90-ena-h-sf-nsp-full-hae-6deg-3mo-job-12",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-ultra",
            containerOverrides={
                "command": [
                    "--instrument",
                    "ultra",
                    "--data-level",
                    "l2",
                    "--descriptor",
                    "u90-ena-h-sf-nsp-full-hae-6deg-3mo",
                    "--start-date",
                    "20250301",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_ultra_l2_u90-ena-h-sf-nsp-full-hae-6deg-3mo-74f0a450_20250301_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )


def test_idex_l2b(session, auth_event):
    """Tests ``lambda_handler` for unique idex l2b case."""
    _static_spice_files(session)
    # Add 2 idex l1b evt files. Although the second file is out of the month range,
    # It should be included in the ProcessingInputCollection because IDEX l2b jobs
    # need housekeeping files that may be before the start date of the cadence job.
    session.add_all(
        [
            ScienceFiles(
                file_path="/path/to/imap_idex_l1b_evt_20230201_v001.cdf",
                instrument="idex",
                data_level="l1b",
                descriptor="evt",
                start_date=datetime(2023, 2, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            ScienceFiles(
                file_path="/path/to/imap_idex_l1b_evt_20230101_v001.cdf",
                instrument="idex",
                data_level="l1b",
                descriptor="evt",
                start_date=datetime(2023, 1, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
        ]
    )
    session.add_all(
        [
            ScienceFiles(
                file_path=f"/path/to/imap_idex_l2a_sci-1week_2023020{day}_v001.cdf",
                instrument="idex",
                data_level="l2a",
                descriptor="sci-1week",
                start_date=datetime(2023, 2, day),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
            for day in [2, 9]
        ]
    )
    session.commit()
    cadence_event = {
        "cadence": "1mo",
    }
    expected_processing_input = ProcessingInputCollection(
        SPICEInput("naif0012.tls", "imap_sclk_0000.tsc"),
        ScienceInput("imap_idex_l2a_sci-1week_20230202_v001.cdf"),
        # There will be 2 science inputs containing l1b evt dependencies.
        # The second input should include both l1b housekeeping files. THe IDEX
        # l2b processing code will deduplicate all of the inputs
        ScienceInput("imap_idex_l1b_evt_20230201_v001.cdf"),
        ScienceInput(
            "imap_idex_l1b_evt_20230201_v001.cdf", "imap_idex_l1b_evt_20230101_v001.cdf"
        ),
    )

    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter"
            ".cadence_to_datetime_range"
        ) as dt_mock,
    ):
        dt_mock.return_value = ("20230209", "20230309")
        lambda_handler(cadence_event, None)
        # Verify the function was called
        mock_batch_client.submit_job.assert_called_with(
            jobName="idex-l2b-all-1mo-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-idex",
            containerOverrides={
                "command": [
                    "--instrument",
                    "idex",
                    "--data-level",
                    "l2b",
                    "--descriptor",
                    "all-1mo",
                    "--start-date",
                    "20230109",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_idex_l2b_all-1mo-9de6e4ae_20230109_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )
    # Assert that reprocessing the cadence file works as expected
    reprocess_params = {
        "reprocessing": "True",
        "start_date": "20230101",
        "end_date": "20231209",
        "instrument": "idex",
        "data_level": "l2b",
        "descriptor": "all-1mo",
    }
    # Create an authenticated event
    reprocessing_event = auth_event({"queryStringParameters": reprocess_params})
    # Move ProcessingJob from in progress to succeeded to mimic the pipeline.
    processing_job_record = session.query(models.ProcessingJob).first()
    processing_job_record.status = models.Status.SUCCEEDED
    session.commit()
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(reprocessing_event, None)
        mock_batch_client.submit_job.assert_called_with(
            jobName="idex-l2b-all-1mo-job-2",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-idex",
            containerOverrides={
                "command": [
                    "--instrument",
                    "idex",
                    "--data-level",
                    "l2b",
                    "--descriptor",
                    "all-1mo",
                    "--start-date",
                    "20230109",
                    "--version",
                    "v002",
                    "--dependency",
                    "imap_idex_l2b_all-1mo-9de6e4ae_20230109_v002.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )

    # Verify the function was called with the correct upstream dependencies
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch(
            "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter"
            ".cadence_to_datetime_range"
        ) as dt_mock,
    ):
        dt_mock.return_value = ("20230209", "20230309")
        lambda_handler(cadence_event, None)
        mock_submit.assert_called_with(
            session,
            {"data_source": "idex", "data_type": "l2b", "descriptor": "all-1mo"},
            "20230109",
            "v002",
            expected_processing_input.serialize(),
        )


def test_invalid_cadence(session):
    """Test that an invalid cadence raises a ValueError."""
    cadence_event = {
        "cadence": "4mo",
    }
    context = {"context": "sample_context"}
    with pytest.raises(ValueError, match="Invalid cadence"):
        lambda_handler(cadence_event, context)


###### HELPER FUNCTION TESTS #######
def test_cadence_to_datetime_range():
    """Test the ``cadence_to_datetime_range`` function."""
    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter.datetime"
    ) as mock_datetime:
        mock_datetime.datetime.today.return_value = datetime(2024, 4, 1)
        mock_datetime.timedelta.side_effect = dt.timedelta
        start_date, end_date = batch_starter.cadence_to_datetime_range(
            cadence="3mo", as_str=True
        )
        assert start_date == "20240101"
        assert end_date == "20240401"

        start_date, end_date = batch_starter.cadence_to_datetime_range(cadence="6mo")
        assert (end_date - start_date) == dt.timedelta(
            days=(CadenceDays.ONE_YEAR.value / 2) - 1
        )

        start_date, end_date = batch_starter.cadence_to_datetime_range(cadence="1yr")
        assert (end_date - start_date) == dt.timedelta(
            days=CadenceDays.ONE_YEAR.value - 1
        )


def test_upload_dependency_file(s3_client, tmp_path, dependency_file, caplog):
    """Test uploading a cadence json file to S3."""
    caplog.set_level("INFO")
    dependencies = ProcessingInputCollection(
        ScienceInput("imap_ultra_l1c_45sensor-pset_20250201_v001.cdf")
    )
    dep_file = imap_data_access.file_validation.DependencyFilePath(
        basename(dependency_file)
    )
    with patch("imap_data_access.config", {"DATA_DIR": tmp_path}):
        dependency_path = pathlib.Path(dep_file.construct_path())
        upload_dependency_file(dependency_path, dependencies.serialize())
    assert "Dependency file uploaded successfully" in caplog.text


def test_determine_max_version(session):
    """Test the ``determine_job_version`` function."""
    # Add an inprogress record to the processing table
    # At the time of job kickoff, we only have these written to the table
    record = ProcessingJob(
        status=models.Status.INPROGRESS,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        version="v001",
        container_command="--dependency imap_lo_l1b_de-27005a05_20100101_v001.json",
    )
    session.add(record)
    session.commit()

    # query the processing table and get the bumped version
    result = determine_job_version(
        session=session,
        instrument="lo",
        data_level="l1b",
        descriptor="de",
        start_date=datetime(2010, 1, 1),
        current_dependencies="abcdsf",
    )
    assert result == "v002"
    # Assert that the version returned is "v001" when the job has not been processed.
    result = determine_job_version(
        session=session,
        instrument="swapi",
        data_level="l1b",
        descriptor="sci",
        start_date=datetime(2010, 1, 1),
        current_dependencies="7f101966",
    )
    assert result == "v001"


def test_determine_job_version_descriptor_is_all(session):
    """Test the ``determine_job_version`` function."""
    _static_spice_files(session)
    # With the descriptor set to "all", the function should return the max version
    # found in the processing job table and not the science files table.
    result = determine_job_version(
        session=session,
        instrument="mag",
        data_level="l1b",
        descriptor="all",
        start_date=datetime(2024, 1, 1),
        current_dependencies="7f101966",
    )
    assert result == "v001"


def test_determine_max_version_missing_processing_job(session):
    """Test that determine_job_version returns the correct version."""
    _static_spice_files(session)
    # Test when processingJob table is not updated, the function checks
    # science_files table to get the version
    result = determine_job_version(
        session=session,
        instrument="swe",
        data_level="l1a",
        descriptor="sci",
        start_date=datetime(2024, 1, 1),
        current_dependencies="7f101966",
    )
    assert result == "v001"


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


def test_dependency_success():
    """Test the handler returns the expected dependency result."""
    dependencies = dependency.get_jobs(
        data_source="swe",
        data_type="l1a",
        descriptor="sci",
        dependency_type="UPSTREAM",
        relationship="HARD",
    )
    assert dependencies == [
        {
            "data_source": "swe",
            "data_type": "l0",
            "descriptor": "raw",
            "relationship": "HARD",
        },
    ]

    # Check for SPICE upstream dependencies
    dependencies = dependency.get_jobs(
        data_source="idex",
        data_type="l1b",
        descriptor="sci-1week",
        relationship="HARD",
        dependency_type="UPSTREAM",
    )
    assert dependencies == [
        {
            "data_source": "idex",
            "data_type": "l1a",
            "descriptor": "sci-1week",
            "relationship": "HARD",
        },
        {
            "data_source": "spin",
            "data_type": "spin",
            "descriptor": "historical",
            "relationship": "HARD",
        },
        {
            "data_source": "ephemeris_reconstructed",
            "data_type": "spice",
            "descriptor": "historical",
            "relationship": "HARD",
        },
        {
            "data_source": "attitude_history",
            "data_type": "spice",
            "descriptor": "historical",
            "relationship": "HARD",
        },
    ]


def test_dependency_success_empty(session):
    """Test that the handler returns the expected dependency result.

    Parameters
    ----------
    session : orm session
        Mock database session.
    """
    dependencies = dependency.get_jobs(
        data_source="swe",
        data_type="l1a",
        descriptor="sci",
        dependency_type="UPSTREAM",
        relationship="HARD",
        start_date="20000101",
        end_date="20000101",
    )
    assert not dependencies


@patch.object(imap_data_access, "download")
@patch.object(batch_starter, "SQS_CLIENT")
def test_repoint_date_range(sqs_mock, mock_download, session, s3_client, tmp_path):
    """Test that the repoint date range is correct."""
    filepath = "imap/hi/l0/2000/02/imap_hi_l0_raw_20000224-repoint00047_v001.pkts"
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")
    # Mock download to return return the test file path
    repoint_file = os.path.join(test_spice_data_dir, "imap_2000_056_03.repoint.csv")
    mock_download.return_value = repoint_file

    sqs_mock.delete_message = Mock()
    sqs_mock.get_queue_url = Mock(return_value="")
    # Write data to the database that batch starter can query
    # for dependencies
    session.add_all(
        [
            ScienceFiles(
                file_path=filepath,
                instrument="hi",
                data_level="l0",
                descriptor="raw",
                start_date=datetime(2000, 2, 24),
                version="v001",
                extension="pkts",
                repointing=47,
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            # Add leapseconds and sclk files to the database
            SPICEFiles(
                file_path="/path/to/naif0012.tls",
                file_name="naif0012.tls",
                ingestion_date=datetime.now(),
                file_root="naif.tls",
                kernel_type="leapseconds",
                min_date_j2000=86400.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[86400, 4575787269]],
                min_date_datetime=datetime(2000, 1, 1),
                max_date_datetime=datetime(2145, 1, 1),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=2,
            ),
            SPICEFiles(
                file_path="/path/to/imap_sclk_0001.tsc",
                file_name="imap_sclk_0001.tsc",
                ingestion_date=datetime.now(),
                file_root="imap_sclk_0001.tsc",
                kernel_type="spacecraft_clock",
                min_date_j2000=86400.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[86400, 4575787269]],
                min_date_datetime=datetime(2000, 1, 1),
                max_date_datetime=datetime(2145, 1, 1),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=2,
            ),
            # Save repoint files to the database
            RepointFiles(
                file_path="/path/to/imap_2000_055_01.repoint.csv",
                end_date=datetime(2000, 2, 24),
                version="01",
                ingestion_date=datetime.now(),
            ),
            RepointFiles(
                file_path="/path/to/imap_2000_056_01.repoint.csv",
                end_date=datetime(2000, 2, 25),
                version="01",
                ingestion_date=datetime.now(),
            ),
            RepointFiles(
                file_path="/path/to/imap_2000_056_02.repoint.csv",
                end_date=datetime(2000, 2, 25),
                version="02",
                ingestion_date=datetime.now(),
            ),
            RepointFiles(
                file_path="/path/to/imap_2000_056_03.repoint.csv",
                end_date=datetime(2000, 2, 25),
                version="03",
                ingestion_date=datetime.now(),
            ),
            RepointFiles(
                file_path="/path/to/imap_2000_060_01.repoint.csv",
                end_date=datetime(2000, 2, 25),
                version="03",
                ingestion_date=datetime.now(),
            ),
            PointingTable(
                pointing_id=47,
                pointing_start_utc=datetime(2000, 2, 24, 0, 0, 0),
                pointing_end_utc=datetime(2000, 2, 25, 0, 0, 0),
                repoint_start_utc=datetime(2000, 2, 24, 0, 0, 0),
                repoint_end_utc=datetime(2000, 2, 25, 0, 0, 0),
            ),
        ]
    )
    session.commit()

    events = {
        "Records": [
            {
                "eventSourceARN": (
                    "arn:aws:sqs:us-east-1:123456789012:test-queue.fifo"
                ),
                "receiptHandle": "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
                "body": '{"detail": '
                '{"object": {"key": "imap/hi/l0/2000/02/'
                'imap_hi_l0_raw_20000225-repoint00047_v001.pkts"}}'
                "}",
            }
        ]
    }
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, None)
        # should call twice, one for Hi all l1a job and one for l1b hk job.
        assert mock_batch_client.submit_job.call_count == 2

    # Test date range for ENA and GLOWS instruments
    filename = "imap_hi_l0_raw_20000224-repoint00047_v001.pkts"
    file_obj = imap_data_access.ScienceFilePath(filename)
    date_range = determine_date_range(session, file_obj)
    assert date_range == ("20000224", "20000225")

    # Now check that other instrument returns expected date range
    filename = "imap_swe_l0_raw_20260926_v001.pkts"
    file_obj = imap_data_access.ScienceFilePath(filename)
    non_repoint_date_range = determine_date_range(session, file_obj)
    assert non_repoint_date_range == ("20260926", "20260926")

    # Check that with no previous repoint file, start date is
    # end_date - 1 day
    filename = "imap_2000_055_01.repoint.csv"
    file_obj = imap_data_access.SPICEFilePath(filename)
    repoint_date_range = determine_date_range(session, file_obj)
    assert repoint_date_range == ("20000223", "20000224")

    # Check that correct start date is returned when there is a previous
    # repoint file.
    # Last repoint file the same day as end date
    filename = "imap_2000_056_03.repoint.csv"
    file_obj = imap_data_access.SPICEFilePath(filename)
    repoint_date_range = determine_date_range(session, file_obj)
    assert repoint_date_range == ("20000225", "20000225")
    # Last repoint several days before end date
    filename = "imap_2000_060_01.repoint.csv"
    file_obj = imap_data_access.SPICEFilePath(filename)
    repoint_date_range = determine_date_range(session, file_obj)
    assert repoint_date_range == ("20000225", "20000229")

    # Add files other needed for the pointing attitude job
    session.add_all(
        [
            SPICEFiles(
                file_path="/path/to/imap_001.tf",
                file_name="imap_001.tf",
                ingestion_date=datetime.now(),
                file_root="imap_.tf",
                kernel_type="imap_frames",
                min_date_j2000=86400.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[86400, 4575787269]],
                min_date_datetime=datetime(2000, 1, 1),
                max_date_datetime=datetime(2145, 1, 1),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=1,
            ),
            SPICEFiles(
                file_path="/path/to/imap_science_0001.tf",
                file_name="imap_science_0001.tf",
                ingestion_date=datetime.now(),
                file_root="imap_science_.tf",
                kernel_type="science_frames",
                min_date_j2000=86400.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[86400, 4575787269]],
                min_date_datetime=datetime(2000, 1, 1),
                max_date_datetime=datetime(2145, 1, 1),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=1,
            ),
            SPICEFiles(
                file_path="path/to/imap_2000_055_2000_056_01.ah.bc",
                file_name="imap_2000_055_2000_056_01.ah.bc",
                ingestion_date=datetime.now(),
                file_root="imap_2000_055_2000_056_.ah.bc",
                kernel_type="attitude_history",
                min_date_j2000=86400.1854936,
                max_date_j2000=4575787269.1854936,
                file_intervals_j2000=[[86400, 4575787269]],
                min_date_datetime=datetime(2000, 1, 1),
                max_date_datetime=datetime(2145, 1, 1),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=1,
            ),
        ]
    )
    session.commit()

    # Test that repoint file ingestion kicks off pointing attitude job
    events = {
        "Records": [
            {
                "eventSourceARN": (
                    "arn:aws:sqs:us-east-1:123456789012:test-queue.fifo"
                ),
                "receiptHandle": "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
                "body": '{"detail": '
                '{"object": {"key": "imap/spice/repoint/imap_2000_056_03.repoint.csv"}}'
                "}",
            }
        ]
    }

    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, None)
        # verify that the function was called once
        mock_batch_client.submit_job.assert_called_once()
        mock_batch_client.submit_job.assert_called_with(
            jobName="spacecraft-l1a-pointing-attitude-job-3",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-spacecraft",
            containerOverrides={
                "command": [
                    "--instrument",
                    "spacecraft",
                    "--data-level",
                    "l1a",
                    "--descriptor",
                    "pointing-attitude",
                    "--start-date",
                    "20000225",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_spacecraft_l1a_pointing-attitude-12ca6ae0_20000225_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )


def test_lambda_skip_processing_due_to_crid_check(session, caplog):
    """Test that processing stops when the calculated CRID is mismatched.

    This indicates that a new upstream file is expected.
    """
    _static_spice_files(session)
    records = [
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_de_20240101_v001.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="de",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            crid="8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
        ),
        ScienceFiles(
            file_path="/path/to/imap_lo_l1b_de_20240101_v002.cdf",
            instrument="lo",
            data_level="l1b",
            descriptor="de",
            start_date=datetime(2010, 1, 1),
            version="v002",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_de_20240102_v002.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="de",
            start_date=datetime(2010, 1, 2),
            version="v002",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_spin_20240101_v001.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="spin",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        SPICEFiles(
            file_path="path/to/imap_recon_20240101_20240101_v01.bsp",
            file_name="imap_recon_20240101_20240101_v01.bsp",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_recon_20240101_20240101_.bsp",
            kernel_type="ephemeris_reconstructed",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/00000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=1,
        ),
        # Add ephemeris predicted file
        SPICEFiles(
            file_path="path/to/imap_pred_20240101_20240101_v01.bsp",
            file_name="imap_pred_20240101_20240101_v01.bsp",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_pred_20240101_20240101_.bsp",
            kernel_type="ephemeris_predicted",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/00000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=1,
        ),
        # Attitude history file
        SPICEFiles(
            file_path="path/to/imap_2024_001_2024_001_01.ah.bc",
            file_name="imap_2024_001_2024_001_01.ah.bc",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_2024_001_2024_001_.ah.bc",
            kernel_type="attitude_history",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/00000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=1,
        ),
        # pointing attitude file
        SPICEFiles(
            file_path="path/to/imap_dps_2024_001_2024_001_01.ah.bc",
            file_name="imap_dps_2024_001_2024_001_01.ah.bc",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_root="imap_dps_2024_001_2024_001_01.ah.bc",
            kernel_type="pointing_attitude",
            min_date_j2000=0,
            max_date_j2000=4575787269.183866,
            file_intervals_j2000=[[0, 4575787269.183866]],
            min_date_datetime=datetime.strptime(
                "2000-01-01 12:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            max_date_datetime=datetime.strptime(
                "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
            file_intervals_datetime="[[2000-01-01T00:00:00, 2145-01-01T00:00:00]]",
            min_date_sclk="1/0000000000:00000",
            max_date_sclk="1/4285909749:39444",
            file_intervals_sclk="[[1/0000000000000:00000, 1/4285909749:39444]]",
            sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
            lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
            version=1,
        ),
        AncillaryFiles(
            file_path="/path/to/imap_lo_bad-times_20240101_v002.csv",
            instrument="lo",
            descriptor="bad-times",
            start_date=datetime(2024, 1, 1),
            version="v002",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        AncillaryFiles(
            file_path="/path/to/imap_lo_sweep-table_20240101_v002.csv",
            instrument="lo",
            descriptor="sweep-table",
            start_date=datetime(2024, 1, 1),
            version="v002",
            extension="csv",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        RepointFiles(
            file_path="/path/to/imap_2024_002_01.repoint.csv",
            end_date=datetime(2024, 1, 2),
            version="01",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        SpinFiles(
            file_path="imap_2024_001_2024_001_01.spin.csv",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            version="01",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap_lo_l1a_de_20240101_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}
    with (
        caplog.at_level(logging.DEBUG)
        and patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
    log = (
        "Found mismatched CRID for /path/to/imap_lo_l1a_de_20240101_v001.cdf. This"
        " indicates that we are expecting a reprocessing for this file."
    )
    # Verify the job was skipped
    assert log in caplog.text
    assert mock_submit.call_count == 0


# Add tests to verify that the correct version is calculated.
def test_determine_job_version_science(session):
    """Tests ``determine_job_version`` for science jobs."""
    # For science files, the job version should be determined from the science files
    # table. Although there is a successful job with v003, the latest science file is
    # v001, so the next version should be v002. It is possible for a job to have
    # Status = SUCCEEDED, but no files were produced for the job, which is why we check
    # the science files table for the version.
    records = [
        ScienceFiles(
            file_path="/path/to/imap_lo_l1a_de_20240101_v002.cdf",
            instrument="lo",
            data_level="l1a",
            descriptor="de",
            start_date=datetime(2024, 1, 1),
            version="v001",
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="lo",
            data_level="l1a",
            descriptor="de",
            start_date=datetime(2024, 1, 1),
            version="v003",
        ),
    ]
    session.add_all(records)
    session.add_all(records)
    version = determine_job_version(
        session, "lo", "l1a", "de", datetime(2024, 1, 1), "test_dependency"
    )
    # The version should be v002
    assert version == "v002"


def test_determine_job_version_spacecraft(session):
    """Tests ``determine_job_version`` for spacecraft jobs."""
    # the function determine_job_version uses the processing job table to determine
    # the correct version for a spacecraft pointing-attitude job, since there is no way
    # to determine the filename and therefore the version from the spice table using the
    # information given. Assert that the version is calculated from the processing
    # job table.
    records = [
        # Add a processing job with version 1
        ProcessingJob(
            status=models.Status.SUCCEEDED,
            instrument="spacecraft",
            data_level="l1a",
            descriptor="pointing-attitude",
            start_date=datetime(2024, 1, 1),
            version="v002",
        )
    ]
    session.add_all(records)
    version = determine_job_version(
        session,
        "spacecraft",
        "l1a",
        "pointing-attitude",
        datetime(2024, 1, 1),
        "test_dependency",
    )
    # The version should be v003 since there was a successful job with v002
    assert version == "v003"
