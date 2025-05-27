"""Tests the batch starter."""

import datetime as dt
import json
import logging
import pathlib
from datetime import datetime
from os.path import basename
from unittest.mock import Mock, call, patch

import imap_data_access
import pandas as pd
import pytest
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    ScienceInput,
    SPICEInput,
)
from sqlalchemy.exc import IntegrityError

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.database.models import (
    AncillaryFiles,
    ProcessingJob,
    ScienceFiles,
    SPICEFiles,
    SpinTable,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    batch_starter,
    dependency,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    CadenceDays,
    calculate_repoint_date_range,
    determine_date_range,
    determine_job_version,
    lambda_handler,
    upload_dependency_file,
)

from .conftest import (
    POSTGRES_AVAILABLE,
    _populate_file_catalog,
)


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
    """Tests ``lambda_handler`` function."""
    _populate_file_catalog(session)
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
                    "v011",
                    "--dependency",
                    "imap_swe_l1a_sci_20240101_v011.json",
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
            dt.datetime(2024, 1, 1, 0, 0),
            "v012",
            processing_input.serialize(),
        )


def test_different_queues(session, s3_client):
    """Tests events from multiple queues."""
    _populate_file_catalog(session)
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
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(multiple_events, context)
        assert mock_batch_client.submit_job.call_count == 2


def test_lambda_handler_ancillary_event(session):
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
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # There should be 2 different jobs submitted for one swe l1b ancillary file
        assert mock_batch_client.submit_job.call_count == 2
        # Assert_called_with only works on the last call
        # Check that the last call is what we expect with the correct dependencies

        # Even though there are two imap_swe_l1b-in-flight-cal ancillary files that
        # have valid dates, there should be only be the most recent one returned
        # as an upstream dep.
        inputs = [
            {"type": "spice", "files": ["naif0012.tls", "imap_sclk_0000.tsc"]},
            {"type": "science", "files": ["imap_swe_l1a_sci_20240102_v001.cdf"]},
            {
                "type": "ancillary",
                "files": ["imap_swe_l1b-in-flight-cal_20230102_v001.cdf"],
            },
            {"type": "ancillary", "files": ["imap_swe_esa-lut_20221231_v001.cdf"]},
            {
                "type": "ancillary",
                "files": ["imap_swe_eu-conversion_20221231_v001.cdf"],
            },
        ]
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
                    "v001",
                    "--dependency",
                    "imap_swe_l1b_sci_20240102_v001.json",
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
            dt.datetime(2024, 1, 2, 0, 0),
            "v002",
            json.dumps(inputs),
        )


def test_lambda_handler_no_dependencies(session):
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
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Verify the function was not called
        assert mock_submit.call_count == 0


def test_lambda_handler_no_dependencies_multiple_files(session):
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
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Verify the function was not called
        assert mock_submit.call_count == 1


def test_lambda_handler_missing_upstream_dependency(session, caplog):
    """Tests ``lambda_handler`` when there are no dependencies for the file."""
    _populate_file_catalog(session)
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
        log_str = (
            "No records found for dependency: "
            "dep={'data_source': 'swe', 'data_type': 'l1b', 'descriptor': 'sci',"
            " 'relationship': 'HARD'}\nstart_date=datetime.datetime(2000,"
            " 1, 2, 0, 0)\nend_date=datetime.datetime(2000, 1, 2, 0, 0)"
        )
        # Verify the info statement was logged.
        assert log_str in caplog.text


def test_lambda_handler_missing_dependency_for_start_date(session, caplog):
    """Tests ``lambda_handler`` function for a specific case."""
    # This test covers a rare scenario: when a new ancillary file is uploaded, the
    # dependency handler might find jobs to run where the uploaded file is valid for the
    # job's start_date, but another required ancillary file is not. The test ensures
    # that in these cases, the job is skipped.

    session.add_all(
        [
            SPICEFiles(
                file_name="naif0012.tls",
                file_path="path/to/naif0012.tls",
                ingestion_date=datetime.strptime(
                    "2025-04-30 18:24:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_root="naif.tls",
                kernel_type="leapseconds",
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
                version=12,
            ),
            SPICEFiles(
                file_name="imap_sclk_0000.tsc",
                file_path="path/to/imap_sclk_0000.tsc",
                ingestion_date=datetime.strptime(
                    "2025-04-30 18:24:01+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_root="imap_sclk_0000.tsc",
                kernel_type="spacecraft_clock",
                min_date_j2000=315576066.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[315576066.1839245, 4575787269.183866]],
                min_date_datetime=datetime.strptime(
                    "2010-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                max_date_datetime=datetime.strptime(
                    "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_intervals_datetime="[[2010-01-01T00:00:00, 2145-01-01T00:00:00]]",
                min_date_sclk="1/0000000000:00000",
                max_date_sclk="1/4285909749:39444",
                file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
                sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
                lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
                version=0,
            ),
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
        ]
    )
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
        "'l2', 'descriptor': 'norm-srf'} with start_date: 20250117 because"
        " of a missing upstream dependency."
    )
    assert expected_log in caplog.text


###### BULK REPROCESSING TESTS #######
def test_bulk_reprocessing_data_level(session, caplog):
    """Tests ``lambda_handler`` when there is bulk reprocessing for a data level."""
    _populate_file_catalog(session)
    # Test with an invalid event first. If data_level is provided, then instrument and
    # descriptor are required.
    events = {
        "queryStringParameters": {
            "reprocessing": "True",
            "start_date": "20230101",
            "end_date": "20260101",
            "data_level": "l1b",
            "descriptor": "sci",
        }
    }
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

    assert mock_submit.call_count == 4


def test_bulk_reprocessing_all(session, caplog):
    """Tests ``lambda_handler`` when there is bulk reprocessing for all instruments."""
    _populate_file_catalog(session)
    # leave instrument, data_level and descriptor blank
    events = {
        "queryStringParameters": {
            "reprocessing": "True",
            "start_date": "20230101",
            "end_date": "20260101",
        }
    }
    context = {"context": "sample_context"}
    # Add instrument and try again
    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
    # There should be two jobs submitted, one for swe, one for lo based off the existing
    # l0 files
    assert mock_submit.call_count == 2


def test_bulk_reprocessing_all_swe(session, caplog):
    """Tests ``lambda_handler`` when there is bulk reprocessing for all instruments."""
    # leave instrument, data_level and descriptor blank
    _populate_file_catalog(session)
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
    assert mock_submit.call_count == 1


###### SPECIAL CASE TESTS #######
def test_ultra_l3_map(session, caplog):
    """Tests ``lambda_handler` for unique ultra l3 map case."""
    # Add 6 months of glows l3e files and 1 ultra l2 map file. These are the
    # dependencies for the ultra l3 map job. The cadence for this example is 3 months of
    # data.
    session.add_all(
        [
            ScienceFiles(
                file_path=f"/path/to/imap_glows_l3e_survival-probability-ul_2024{month:02}01_v001.cdf",
                instrument="glows",
                data_level="l3e",
                descriptor="survival-probability-ul",
                start_date=datetime(2024, month, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
            for month in range(2, 9)
        ]
    )
    session.add_all(
        [
            ScienceFiles(
                file_path="/path/to/imap_ultra_l2_u90-ena-h-sf-nsp-full-hae-4deg-3mo_20240201_v001.cdf",
                instrument="ultra",
                data_level="l2",
                descriptor="u90-ena-h-sf-nsp-full-hae-4deg-3mo",
                start_date=datetime(2024, 2, 1),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            SPICEFiles(
                file_name="naif0012.tls",
                file_path="path/to/naif0012.tls",
                ingestion_date=datetime.strptime(
                    "2025-04-30 18:24:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_root="naif.tls",
                kernel_type="leapseconds",
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
                version=12,
            ),
            SPICEFiles(
                file_name="imap_sclk_0000.tsc",
                file_path="path/to/imap_sclk_0000.tsc",
                ingestion_date=datetime.strptime(
                    "2025-04-30 18:24:01+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_root="imap_sclk_0000.tsc",
                kernel_type="spacecraft_clock",
                min_date_j2000=315576066.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[315576066.1839245, 4575787269.183866]],
                min_date_datetime=datetime.strptime(
                    "2010-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                max_date_datetime=datetime.strptime(
                    "2145-01-01 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
                file_intervals_datetime="[[2010-01-01T00:00:00, 2145-01-01T00:00:00]]",
                min_date_sclk="1/0000000000:00000",
                max_date_sclk="1/4285909749:39444",
                file_intervals_sclk="[[1/0000000000:00000, 1/4285909749:39444]]",
                sclk_kernel="/mnt/data/imap/spice/sclk/imap_sclk_0001.tsc",
                lsk_kernel="/mnt/data/imap/spice/lsk/naif0012.tls",
                version=0,
            ),
        ]
    )
    session.commit()
    events = {
        # This event is for the ultra l3 map job. The glows l3e files may come in
        # groupings. We want to test that only one job is submitted.
        "Records": [
            {
                "body": '{"detail": {"object": {"key": '
                '"imap_glows_l3e_survival-probability-ul_20240201_v001.cdf"}}'
                "}"
            },
            {
                "body": '{"detail": {"object": {"key": '
                '"imap_glows_l3e_survival-probability-ul_20240301_v001.cdf"}}'
                "}"
            },
        ]
    }
    context = {"context": "sample_context"}
    expected_processing_input = ProcessingInputCollection()

    spice_files = ["naif0012.tls", "imap_sclk_0000.tsc"]
    expected_processing_input.add(SPICEInput(*spice_files))

    # There will be 3 glows l3e files (representing 3 months) that are used as
    # dependencies for the job.
    # NOTE: in reality, there will be more than 3 glows l3e files.
    glows_files = [
        f"imap_glows_l3e_survival-probability-ul_20240{month}01_v001.cdf"
        for month in range(2, 6)
    ]
    expected_processing_input.add(ScienceInput(*glows_files))
    expected_processing_input.add(
        ScienceInput(
            "imap_ultra_l2_u90-ena-h-sf-nsp-full-hae-4deg-3mo_20240201_v001.cdf"
        )
    )
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Verify the function was called
        mock_batch_client.submit_job.assert_called_with(
            jobName="ultra-l3-u90-ena-h-sf-sp-full-hae-4deg-3mo-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-ultra-l3",
            containerOverrides={
                "command": [
                    "--instrument",
                    "ultra",
                    "--data-level",
                    "l3",
                    "--descriptor",
                    "u90-ena-h-sf-sp-full-hae-4deg-3mo",
                    "--start-date",
                    "20240201",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_ultra_l3_u90-ena-h-sf-sp-full-hae-4deg-3mo_20240201_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )
        # Assert that only one job is submitted.
        assert mock_batch_client.submit_job.call_count == 1
        assert "Already tried to submit job from a GLOWS l3e file." in caplog.text

        # Assert that the try_to_submit_job function was called with the correct
        # upstream dependencies
        with (
            patch.object(batch_starter, "try_to_submit_job") as mock_submit,
            patch.object(batch_starter, "generate_queue_url", return_value=False),
        ):
            lambda_handler(events, context)
            mock_submit.assert_called_with(
                session,
                {
                    "data_source": "ultra",
                    "data_type": "l3",
                    "descriptor": "u90-ena-h-sf-sp-full-hae-4deg-3mo",
                },
                dt.datetime(2024, 2, 1, 0, 0),
                "v002",
                expected_processing_input.serialize(),
            )


def test_bulk_reprocessing_special_case(session):
    """Tests ``lambda_handler`` for a special case in bulk reprocessing."""
    # Add test data to the database for the special case
    # Add hi l2 files. Some of these will be used as dependencies for the job.
    _populate_file_catalog(session)
    # TODO fix dates to be more accurate
    session.add_all(
        [
            ScienceFiles(
                file_path=f"/path/to/imap_hi_l2_h90-ena-h-sf-nsp-ram-hae-4deg-6mo_{date}_v001.cdf",
                instrument="hi",
                data_level="l2",
                descriptor="h90-ena-h-sf-nsp-ram-hae-4deg-6mo",
                start_date=datetime.strptime(date, "%Y%m%d"),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
            for date in ["20240202", "20240803"]
        ]
    )
    session.add_all(
        [
            ScienceFiles(
                file_path=f"/path/to/imap_hi_l2_h90-ena-h-sf-nsp-anti-hae-4deg-6mo_{date}_v001.cdf",
                instrument="hi",
                data_level="l2",
                descriptor="h90-ena-h-sf-nsp-anti-hae-4deg-6mo",
                start_date=datetime.strptime(date, "%Y%m%d"),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            )
            for date in ["20240202", "20240803"]
        ]
    )
    session.commit()

    # Bulk reprocessing event for the special case
    events = {
        "queryStringParameters": {
            "reprocessing": "True",
            "start_date": "20240101",
            "end_date": "20250210",
            "instrument": "hi",
            "data_level": "l3",
            "descriptor": "h90-ena-h-sf-sp-full-hae-4deg-6mo",
        }
    }
    context = {"context": "None"}

    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, context)
        # Verify that the hi l3 jobs were reprocessed
        assert mock_batch_client.submit_job.call_count == 2


def test_lambda_handler_mag_l1c_case(session):
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
                    "imap_mag_l1c_norm-mago_20240101_v001.json",
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
                    "imap_mag_l1c_norm-mago_20240101_v002.json",
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
            dt.datetime(2024, 1, 1, 0, 0),
            "v003",
            expected_processing_input.serialize(),
        )


def test_lambda_handler_duplicate_mag_l1c_job(session, caplog):
    """Tests ``lambda_handler` skips processing for a duplicate job."""
    # Mock the situation where mag l1b files trigger batch starter back to back but
    # with the same exact dependencies.
    # We should expect the duplicate job to be skipped.
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
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
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

        assert (
            "This job is a duplicate of the previous job. See file:"
            " imap_mag_l1c_norm-mago_20240101_v001.cdf."
            " Skipping submission."
        ) in caplog.text


### TEST CADENCE EVENT
def test_def_cadence_map_event(setup_s3, session, tmp_path):
    """Test that a cadence event kicks off the right processing job."""
    _populate_file_catalog(session)
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
                    "imap_ultra_l2_u90-ena-h-sf-nsp-full-hae-6deg-3mo_20250301_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )


def test_idex_l2b(session):
    """Tests ``lambda_handler` for unique idex l2b case."""
    _populate_file_catalog(session)
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
            jobName="idex-l2b-sci-1mo-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-idex",
            containerOverrides={
                "command": [
                    "--instrument",
                    "idex",
                    "--data-level",
                    "l2b",
                    "--descriptor",
                    "sci-1mo",
                    "--start-date",
                    "20230109",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_idex_l2b_sci-1mo_20230109_v001.json",
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
            {"data_source": "idex", "data_type": "l2b", "descriptor": "sci-1mo"},
            dt.datetime(2023, 1, 9, 0, 0),
            "v001",
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
        assert start_date == "20231231"
        assert end_date == "20240401"

        start_date, end_date = batch_starter.cadence_to_datetime_range(cadence="6mo")
        assert (end_date - start_date) == dt.timedelta(
            days=CadenceDays.ONE_YEAR.value / 2
        )

        start_date, end_date = batch_starter.cadence_to_datetime_range(cadence="1yr")
        assert (end_date - start_date) == dt.timedelta(days=CadenceDays.ONE_YEAR.value)


def test_upload_dependency_file(s3_client, tmp_path, cadence_file, caplog):
    """Test uploading a cadence json file to S3."""
    caplog.set_level("INFO")
    dependencies = ProcessingInputCollection(
        ScienceInput("imap_ultra_l1c_45sensor-pset_20250201_v001.cdf")
    )
    cadence_file = imap_data_access.file_validation.CadenceFilePath(
        basename(cadence_file)
    )
    with patch("imap_data_access.config", {"DATA_DIR": tmp_path}):
        cadence_dependency_path = pathlib.Path(cadence_file.construct_path())
        upload_dependency_file(cadence_dependency_path, dependencies.serialize())
    assert "Dependency file uploaded successfully" in caplog.text


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
    # found in the processing job table and not the science files table.
    result = determine_job_version(
        session=session,
        instrument="mag",
        data_level="l1b",
        descriptor="all",
        start_date=datetime(2024, 1, 1),
    )
    assert result == "v001"


def test_determine_max_version_missing_processing_job(session):
    """Test that determine_job_version returns the correct version."""
    _populate_processing_table(session)
    _populate_file_catalog(session)
    # Test when processingJob table is not updated, the function checks
    # science_files table to get the version
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


def test_spice_event(session, s3_client):
    """Test spice dependencies."""
    # Write repoint file to s3 that batch starter can query
    # for dependencies
    filepath = "imap/spice/repoint/imap_2025_120_01.repoint.csv"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test",
    )
    # Write data to the database that batch starter can query
    # for dependencies
    session.add_all(
        [
            # Data to test the SPICE dependency using IDEX L1B and pointing_attitude
            ScienceFiles(
                file_path="/path/to/imap_idex_l1a_sci-1week_20250429_v001.cdf",
                instrument="idex",
                data_level="l1a",
                descriptor="sci-1week",
                start_date=datetime(2025, 4, 29),
                version="v001",
                extension="cdf",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            SPICEFiles(
                # 2025028 to 2025030
                file_name="imap_2025_118_2025_120_02.ah.bc",
                file_path="/path/to/imap_2025_118_2025_120_02.ah.bc",
                ingestion_date=datetime.now(),
                file_root="imap_2025_118_2025_120_.ah.bc",
                kernel_type="attitude_history",
                min_date_j2000=799070400.1854936,
                max_date_j2000=799243200.185493,
                file_intervals_j2000=[[799070400, 799243200]],
                min_date_datetime=datetime(2025, 4, 28),
                max_date_datetime=datetime(2025, 4, 30),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=2,
            ),
            SPICEFiles(
                file_name="imap_recon_20250428_20250430_v02.bsp",
                file_path="/path/to/imap_recon_20250428_20250430_v02.bsp",
                ingestion_date=datetime.now(),
                file_root="imap_recon_20250428_20250430_v.bsp",
                kernel_type="ephemeris_reconstructed",
                min_date_j2000=799070400.1854936,
                max_date_j2000=799243200.185493,
                file_intervals_j2000=[[799070400, 799243200]],
                min_date_datetime=datetime(2025, 4, 28),
                max_date_datetime=datetime(2025, 4, 30),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=2,
            ),
            SpinTable(
                # 2025028 to 2025030
                file_path="/mnt/data/imap/spice/spin/imap_2025_118_2025_120_01.spin.csv",
                start_date=datetime(2025, 4, 28),
                end_date=datetime(2025, 4, 30),
                version="01",
                ingestion_date=datetime.now(),
            ),
        ]
    )
    session.commit()
    # Event of spin file should trigger IDEX L1B sci-1week job
    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": '
                '{"key": "imap/spice/spin/imap_2025_118_2025_120_01.spin.csv"}}'
                "}"
            }
        ]
    }
    expected_dependency_input = [
        {
            "type": "spin",
            "files": ["imap_2025_118_2025_120_01.spin.csv"],
        },
        {
            "type": "spice",
            "files": [
                "imap_recon_20250428_20250430_v02.bsp",
                "imap_2025_118_2025_120_02.ah.bc",
            ],
        },
        {
            "type": "science",
            "files": [
                "imap_idex_l1a_sci-1week_20250429_v001.cdf",
            ],
        },
    ]
    with (
        patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, None)
        mock_batch_client.submit_job.assert_called_once()
        mock_batch_client.submit_job.assert_called_with(
            jobName="idex-l1b-sci-1week-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-idex",
            containerOverrides={
                "command": [
                    "--instrument",
                    "idex",
                    "--data-level",
                    "l1b",
                    "--descriptor",
                    "sci-1week",
                    "--start-date",
                    "20250429",
                    "--version",
                    "v001",
                    "--dependency",
                    "imap_idex_l1b_sci-1week_20250429_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )

    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
        patch.object(batch_starter, "generate_queue_url", return_value=False),
    ):
        lambda_handler(events, None)
        mock_submit.assert_called_with(
            session,
            {"data_source": "idex", "data_type": "l1b", "descriptor": "sci-1week"},
            dt.datetime(2025, 4, 29, 0, 0),
            "v002",
            json.dumps(expected_dependency_input),
        )


@patch.object(imap_data_access, "download")
def test_repoint_date_range(mock_download, session, s3_client, tmp_path):
    """Test that the repoint date range is correct."""
    filepath = "imap/hi/l0/2026/09/imap_hi_l0_raw_20260926-repoint00002_v001.pkts"
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=filepath,
        Body=b"test",
    )
    repoint_df = pd.DataFrame(
        {
            "repoint_start_sec_sclk": [528026403, 528112803, 528199203],
            "repoint_start_subsec_sclk": [0, 0, 0],
            "repoint_end_sec_sclk": [528028443, 528114843, 528201243],
            "repoint_end_subsec_sclk": [0, 0, 0],
            "repoint_start_utc": [
                "2026-09-25 10:00:00.000",
                "2026-09-26 10:00:00.000",
                "2026-09-27 10:00:00.000",
            ],
            "repoint_end_utc": [
                "2026-09-25 10:00:00.000",
                "2026-09-26 10:00:00.000",
                "2026-09-27 10:00:00.000",
            ],
            "repoint_id": [1, 2, 3],
        }
    )
    # Write this repoint data to s3 file
    s3_client.put_object(
        Bucket="test-data-bucket",
        Key="imap/spice/repoint/imap_2026_269_02.repoint.csv",
        Body=repoint_df.to_csv(index=False).encode("utf-8"),
    )
    # Mock download to return return the test file path
    repoint_file = tmp_path / "imap_2026_269_02.repoint.csv"
    repoint_df.to_csv(repoint_file, index=False)
    mock_download.return_value = repoint_file

    # Write data to the database that batch starter can query
    # for dependencies
    session.add_all(
        [
            ScienceFiles(
                file_path=filepath,
                instrument="hi",
                data_level="l0",
                descriptor="raw",
                start_date=datetime(2026, 9, 26),
                version="v001",
                extension="pkts",
                ingestion_date=datetime.strptime(
                    "2024-01-25 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
                ),
            ),
            # Add leapseconds and sclk files to the database
            SPICEFiles(
                file_name="naif0012.tls",
                ingestion_date=datetime.now(),
                file_root="naif.tls",
                kernel_type="leapseconds",
                min_date_j2000=315576066.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[315576066, 4575787269]],
                min_date_datetime=datetime(2010, 1, 1),
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
                file_name="imap_sclk_0001.tsc",
                ingestion_date=datetime.now(),
                file_root="imap_sclk_0001.tsc",
                kernel_type="spacecraft_clock",
                min_date_j2000=315576066.1839245,
                max_date_j2000=4575787269.183866,
                file_intervals_j2000=[[315576066, 4575787269]],
                min_date_datetime=datetime(2010, 1, 1),
                max_date_datetime=datetime(2145, 1, 1),
                file_intervals_datetime=[["0", "0"]],
                min_date_sclk="",
                max_date_sclk="",
                file_intervals_sclk=[["0", "0"]],
                sclk_kernel="imap_sclk_0001.tsc",
                lsk_kernel="naif0012.tls",
                version=2,
            ),
        ]
    )
    session.commit()

    events = {
        "Records": [
            {
                "body": '{"detail": '
                '{"object": {"key": "imap/hi/l0/2026/09/'
                'imap_hi_l0_raw_20260926-repoint00002_v001.pkts"}}'
                "}"
            }
        ]
    }
    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
        lambda_handler(events, None)
        # should call twice, one for Hi all l1a job and one for l1b hk job.
        assert mock_batch_client.submit_job.call_count == 2

    filename = "imap_hi_l0_raw_20260926-repoint00002_v001.pkts"
    file_obj = imap_data_access.ScienceFilePath(filename)

    date_range = determine_date_range(file_obj)
    assert date_range == ("20260926", "20260927")

    repoint_date_range = calculate_repoint_date_range(file_obj)
    assert repoint_date_range == ("20260926", "20260927")

    filename = "imap_swe_l0_raw_20260926_v001.pkts"
    file_obj = imap_data_access.ScienceFilePath(filename)
    non_repoint_date_range = determine_date_range(file_obj)
    assert non_repoint_date_range == ("20260926", "20260926")


def test_lambda_skip_processing_due_to_crid_check(session, caplog):
    """Test that processing stops when the calculated CRID is mismatched.

    This indicates that a new upstream file is expected.
    """
    _populate_file_catalog(session)
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