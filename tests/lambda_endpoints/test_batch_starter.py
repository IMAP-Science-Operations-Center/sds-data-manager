"""Tests the batch starter."""

import json
import logging
from datetime import datetime
from unittest.mock import Mock, patch

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
    SPICEFiles,
    SpinTable,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    batch_starter,
    dependency,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.batch_starter import (
    determine_job_version,
    lambda_handler,
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


def test_lambda_handler(session):
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
    serialized_processing_input = [
        {"type": "spice", "files": ["naif0012.tls", "imap_sclk_0000.tsc"]},
        {"type": "science", "files": ["imap_swe_l0_raw_20240110_v001.pkts"]},
    ]

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
                    json.dumps(serialized_processing_input),
                    "--upload-to-sdc",
                ]
            },
        )


def test_lambda_handler_multiple_events(session):
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
                    expected_processing_input.serialize(),
                    "--upload-to-sdc",
                ]
            },
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
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
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
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
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
                '{"object": {"key": "imap_swe_l1b_sci_20000101_v001.cdf"}}'
                "}"
            }
        ]
    }
    context = {"context": "sample_context"}
    with caplog.at_level(logging.DEBUG):
        lambda_handler(events, context)
        log_str = (
            "No records found for dependency: "
            "dep={'data_source': 'swe', 'data_type': 'l1b', 'descriptor': 'sci',"
            " 'relationship': 'HARD'}\nstart_date=datetime.datetime(2000,"
            " 1, 1, 0, 0)\nend_date=datetime.datetime(2000, 1, 1, 0, 0)"
        )
        # Verify the info statement was logged.
        assert log_str in caplog.text


###### BULK REPROCESSING TESTS #######
def test_bulk_reprocessing_data_level(session, caplog):
    """Tests ``lambda_handler`` when there is bulk reprocessing for a data level."""
    _populate_file_catalog(session)
    # Test with an invalid event first. If data_level is provided, then instrument and
    # descriptor are required.
    events = {
        "queryStringParameters": {
            "reprocessing": True,
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
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
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
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
        lambda_handler(events, context)
    # There should be two jobs submitted, one for swe, one for lo based off the existing
    # l0 files
    assert mock_submit.call_count == 2


def test_bulk_reprocessing_all_swe(session, caplog):
    """Tests ``lambda_handler`` when there is bulk reprocessing for all instruments."""
    _populate_file_catalog(session)
    # leave instrument, data_level and descriptor blank
    events = {
        "queryStringParameters": {
            "reprocessing": "True",
            "start_date": "20230101",
            "end_date": "20260101",
            "instrument": "swe",
        }
    }
    context = {"context": "sample_context"}
    # Add instrument and try again
    with patch.object(batch_starter, "try_to_submit_job") as mock_submit:
        lambda_handler(events, context)
    # There should be one job submitted for swe
    assert mock_submit.call_count == 1


###### HELPER FUNCTION TESTS #######
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

    with patch.object(batch_starter, "BATCH_CLIENT", Mock()) as mock_batch_client:
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
                    json.dumps(expected_dependency_input),
                    "--upload-to-sdc",
                ]
            },
        )
