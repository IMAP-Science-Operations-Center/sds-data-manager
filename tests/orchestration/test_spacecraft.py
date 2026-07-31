"""Testing the spacecraft job kickoff and job submission.

This test sets up the environment so that:
    - Pointing numbers start on 2026-01-01, and span nearly the whole day.
    - There are 2 spice files in the database: sclk and lsk
    - The pointing number partition sensor has already ran in dagster
    - There are no other assets within Dagster
"""

# source $(poetry env info --path)/bin/activate
# poetry run pytest tests/orchestration/test_spacecraft.py -s
import datetime

import imap_data_access
import pytest
from dagster import (
    build_asset_context,
    build_sensor_context,
)

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.imap_dagster import defs, job_handlers


def _irrelevant_spice_data():
    """Populate irrelevant columns in DB with dummy data."""
    irrelevant_data = {
        "min_date_datetime": datetime.datetime.now(),
        "max_date_datetime": datetime.datetime.now(),
        "file_intervals_datetime": [["0", "0"]],
        "min_date_sclk": "",
        "max_date_sclk": "",
        "file_intervals_sclk": [["0", "0"]],
        "sclk_kernel": "nothing",
        "lsk_kernel": "nothing",
    }
    return irrelevant_data


def _insert_spice_file(session, filename, intervals, upload_time=0):
    spice_object = imap_data_access.SPICEFilePath(filename)
    version = spice_object.spice_metadata["version"]
    metadata_params = {
        "file_name": filename,
        "file_path": f"imap/spice/{filename}",
        "file_root": "".join(filename.rsplit(version, 1)),
        "kernel_type": spice_object.spice_metadata["type"],
        "version": version,
        "file_intervals_j2000": intervals,
        "min_date_j2000": intervals[0][0],
        "max_date_j2000": intervals[-1][1],
        "ingestion_date": datetime.datetime.now() + datetime.timedelta(upload_time),
    } | _irrelevant_spice_data()
    session.add(models.SPICEFiles(**metadata_params))
    session.commit()


def test_spacecraft_l1a_sensor(mock_db_session, ephemeral_instance):
    # Get the actual sensor function from the dagster definitions
    spacecraft_l1a_sensor = defs.get_sensor_def(
        "spacecraft_l1a_pointingattitude_kickoff_sensor"
    )

    # Use a built-in dagster test function to create a context object
    context = build_sensor_context(instance=ephemeral_instance)

    # Run the sensor evaluation
    sensor_result = spacecraft_l1a_sensor(context)
    run_requests = list(sensor_result)

    # Verify things were kicked off
    assert len(run_requests) == 10, "Expected a run for each repoint partition."


def test_spacecraft_l1a_no_repoint(
    mock_db_session, ephemeral_instance, insert_test_spice_files
):
    # Use a built-in dagster function for testing
    context = build_asset_context(
        partition_key="repoint2_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

    # Find the spacecraft_l1a_pointingattitude_processing_job and call it.
    spacecraft_l1a_job = next(
        (
            job
            for job in job_handlers
            if job.dagster_job_name == "spacecraft_l1a_pointingattitude_processing_job"
        ),
        None,
    )
    assert spacecraft_l1a_job is not None, (
        "spacecraft_l1a_pointingattitude_processing_job was not found in job_handlers"
    )

    # Add in SPICE files
    _insert_spice_file(
        mock_db_session, "imap_2026_001_2026_100_001.ah.bc", [[1, 10000000000000]]
    )
    _insert_spice_file(mock_db_session, "imap_120.tf", [[1, 10000000000000]])
    _insert_spice_file(mock_db_session, "imap_science_120.tf", [[1, 10000000000000]])

    # Run the asset and verify an error is thrown about missing the repoint file.
    with pytest.raises(ValueError, match="Repoint"):
        list(spacecraft_l1a_job.run_job(context, 1, 1))


def test_spacecraft_l1a_no_spice(mock_db_session, ephemeral_instance):
    # Use a built-in dagster function for testing
    context = build_asset_context(
        partition_key="repoint2_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

    # Find the spacecraft_l1a_pointingattitude_processing_job and call it.
    spacecraft_l1a_job = next(
        (
            job
            for job in job_handlers
            if job.dagster_job_name == "spacecraft_l1a_pointingattitude_processing_job"
        ),
        None,
    )
    assert spacecraft_l1a_job is not None, (
        "spacecraft_l1a_pointingattitude_processing_job was not found in job_handlers"
    )
    mock_db_session.add(
        models.RepointFiles(
            file_path="imap_2045_001_01.repoint",
            end_date=datetime.datetime(2045, 1, 1),
            version="01",
            ingestion_date=datetime.datetime(2000, 1, 1),
            released=False,
        )
    )

    # Verify nothing has happened still, because SPICE is still missing.
    yielded_files = list(spacecraft_l1a_job.run_job(context, 1, 1))
    assert len(yielded_files) == 0
    assert len(mock_db_session.query(models.ProcessingJob).all()) == 0


def test_spacecraft_l1a_submits(
    mock_db_session, ephemeral_instance, insert_test_spice_files
):
    context = build_asset_context(
        partition_key="repoint2_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

    # Find the spacecraft_l1a_pointingattitude_processing_job and call it.
    spacecraft_l1a_job = next(
        (
            job
            for job in job_handlers
            if job.dagster_job_name == "spacecraft_l1a_pointingattitude_processing_job"
        ),
        None,
    )
    assert spacecraft_l1a_job is not None, (
        "spacecraft_l1a_pointingattitude_processing_job was not found in job_handlers"
    )

    mock_db_session.add(
        models.RepointFiles(
            file_path="imap_2045_001_01.repoint",
            end_date=datetime.datetime(2045, 1, 1),
            version="01",
            ingestion_date=datetime.datetime(2000, 1, 1),
            released=False,
        )
    )
    _insert_spice_file(
        mock_db_session, "imap_2026_001_2026_100_001.ah.bc", [[1, 10000000000000]]
    )
    _insert_spice_file(mock_db_session, "imap_120.tf", [[1, 10000000000000]])
    _insert_spice_file(mock_db_session, "imap_science_120.tf", [[1, 10000000000000]])

    # Run the asset and verify we get to the end, showing a batch job was submitted
    list(spacecraft_l1a_job.run_job(context, 1, 1))

    # Show that a Batch job was submitted
    assert len(mock_db_session.query(models.ProcessingJob).all()) == 1
