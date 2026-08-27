"""Testing an end-to-end job kickoff that covers multiple areas."""

# test_glows_l1a_sensor.py

# source $(poetry env info --path)/bin/activate
# poetry run pytest tests/orchestration/test_end_to_end.py -s
import datetime

from dagster import (
    AssetKey,
    AssetMaterialization,
    AssetObservation,
    build_asset_context,
    build_sensor_context,
)

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.imap_dagster import defs, job_handlers


def test_glows_l1a_end_to_end(
    mock_db_session, ephemeral_instance, insert_test_spice_files
):
    """Test the GLOWS L1a orchestration from Dagster.

    This test sets up the environment so that:
      - Pointing numbers start on 2026-01-01, and span nearly the whole day.
      - There are 2 spice files in the database: sclk and lsk
      - The pointing number partition sensor has already ran in dagster
      - There are no other assets within Dagster

    """
    ### SENSOR TEST

    # Get the actual sensor function from the dagster definitions
    glows_l1a_sensor = defs.get_sensor_def("glows_l1a_all_kickoff_sensor")

    # Use a built-in dagster test function to create a context object
    context = build_sensor_context(instance=ephemeral_instance)

    # Run the sensor evaluation
    sensor_result = glows_l1a_sensor(context)
    run_requests = list(sensor_result)

    # TEST 1: Verify nothing was kicked off
    assert len(run_requests) == 0, "Expected no RunRequests to be yielded."

    # Simulate a glows L0 file arriving.
    ephemeral_instance.report_runless_asset_event(
        asset_event=AssetMaterialization(
            asset_key=AssetKey(["glows_l0_raw"]),
            partition="repoint2_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
            description="Mocked arrival of L0 science file for testing.",
            metadata={
                "file_names": [
                    "imap_glows_l0_raw_20260102-repoint00002_v000.0001.pkts",
                ],
                "input_type": "science",
                "version": "v000.0001",
                "start_date": "",
            },
        )
    )

    # Re-build the sensor context, now with the new materialization
    context = build_sensor_context(instance=ephemeral_instance)

    # Run the sensor evaluation
    sensor_result = glows_l1a_sensor(context)
    run_requests = list(sensor_result)

    # TEST 2: Verify a run was actually kicked off by the sensor
    assert len(run_requests) == 1, "Expected exactly one RunRequest to be yielded."

    ### ASSET TESTS

    # Use a built-in dagster function for testing
    context = build_asset_context(
        partition_key="repoint2_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

    # Find the glows_l1a_all_processing_job and call it.
    glows_l1a_job = next(
        (
            job
            for job in job_handlers
            if job.dagster_job_name == "glows_l1a_all_processing_job"
        ),
        None,
    )
    assert glows_l1a_job is not None, (
        "glows_l1a_all_processing_job was not found in job_handlers"
    )
    # TEST 3: Run the asset and verify that a job was submitted.
    # The op no longer materializes anything itself, it only reports an
    # AssetObservation for each output once the Batch job succeeds.
    yielded_files = list(glows_l1a_job.run_job(context, 1, 1))
    assert len(mock_db_session.query(models.ProcessingJob).all()) == 1
    assert len(yielded_files) == len(glows_l1a_job.job_config.outputs)
    assert all(isinstance(f, AssetObservation) for f in yielded_files)

    # TEST 4: Run the job again.
    # We expect it to simply report the job was already submitted,
    # because this exact job was run previously
    yielded_files = list(glows_l1a_job.run_job(context, 1, 1))
    assert len(mock_db_session.query(models.ProcessingJob).all()) == 1
    assert len(yielded_files) == len(glows_l1a_job.job_config.outputs)
    assert all(isinstance(f, AssetObservation) for f in yielded_files)

    # Insert pretend data into ScienceFiles
    glows_l1a_de_file = models.ScienceFiles(
        file_path="imap_glows_l1a_de_20260102_v001.0001.cdf",
        instrument="glows",
        data_level="l1a",
        descriptor="de",
        start_date=datetime.datetime(2026, 1, 2),
        repointing=2,
        major_version=1,
        minor_version=1,
        ingestion_date=datetime.datetime(2026, 1, 2),
        cr=1,
        crid="asdf",
        released=False,
        extension="cdf",
    )
    mock_db_session.add(glows_l1a_de_file)
    glows_l1a_hist_file = models.ScienceFiles(
        file_path="imap_glows_l1a_hist_20260102_v001.0001.cdf",
        instrument="glows",
        data_level="l1a",
        descriptor="hist",
        start_date=datetime.datetime(2026, 1, 2),
        repointing=2,
        major_version=1,
        minor_version=1,
        ingestion_date=datetime.datetime(2026, 1, 2),
        cr=1,
        crid="asdf",
        released=False,
        extension="cdf",
    )
    mock_db_session.add(glows_l1a_hist_file)
    mock_db_session.commit()

    # TEST 5: The op still does not materialize anything itself.
    yielded_files = list(glows_l1a_job.run_job(context, 1, 1))
    assert len(yielded_files) == len(glows_l1a_job.job_config.outputs)
    assert all(isinstance(f, AssetObservation) for f in yielded_files)

    # TEST 6: The consolidated materialization sensor should find the new
    # science files and runlessly materialize the corresponding assets.
    materialization_sensor = defs.get_sensor_def("science_file_materialization_sensor")
    context = build_sensor_context(instance=ephemeral_instance)
    sensor_result = materialization_sensor(context)

    assert len(sensor_result.asset_events) == 2
    for materialization in sensor_result.asset_events:
        assert isinstance(materialization, AssetMaterialization)
        assert materialization.metadata["file_names"].value[0] in (
            "imap_glows_l1a_de_20260102_v001.0001.cdf",
            "imap_glows_l1a_hist_20260102_v001.0001.cdf",
        )
    # Assert only one job has ever been submitted still
    assert len(mock_db_session.query(models.ProcessingJob).all()) == 1
