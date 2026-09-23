"""Testing the spacecraft job kickoff and job submission.

This test sets up the environment so that:
    - Pointing numbers start on 2026-01-01, and span nearly the whole day.
    - There are 2 spice files in the database: sclk and lsk
    - The repoint, daily, and pointing_attitude partition sensors have
      already run in dagster
    - There are no other assets within Dagster
"""

# source $(poetry env info --path)/bin/activate
# poetry run pytest tests/orchestration/test_spacecraft.py -s
import datetime
import json

import imap_data_access
import pytest
from dagster import (
    AssetObservation,
    build_asset_context,
    build_sensor_context,
)
from imap_data_access import processing_input

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

    # This job is keyed off pointing_attitude_partitions, not repoint_partitions:
    # one partition per ah kernel cycle. `ephemeral_instance` seeds exactly one ah
    # kernel covering the full pointing_table_entries range, so one partition (and
    # therefore one run) is expected here, not one per pointing.
    assert len(run_requests) == 1, "Expected a run for the single ah kernel partition."


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

    # Verify that we have only returned an asset observation
    yielded_files = list(spacecraft_l1a_job.run_job(context, 1, 1))
    assert len(mock_db_session.query(models.ProcessingJob).all()) == 0
    assert isinstance(yielded_files[0], AssetObservation)


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


def test_spacecraft_l1a_find_outputs_materializes_from_spice_files(
    mock_db_session, ephemeral_instance
):
    """The pointing-attitude output is a SPICE kernel, not a science file.

    Regression test: this job's output is indexed into models.SPICEFiles
    (kernel_type "pointing_attitude"), not models.ScienceFiles. find_outputs()
    must be able to locate it there and emit a materialization, or Dagster
    will never show this asset as materialized even after a successful run.
    """
    context = build_asset_context(
        partition_key="pointingattitude_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

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

    target_start = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    target_end = datetime.datetime(2026, 1, 2, 23, 59, 59, tzinfo=datetime.timezone.utc)

    mock_db_session.add(
        models.SPICEFiles(
            file_path="imap/spice/ck/imap_dps_2026_002_2026_002_01.ah.bc",
            file_name="imap_dps_2026_002_2026_002_01.ah.bc",
            file_root="imap_dps_2026_002_2026_002_.ah.bc",
            kernel_type="pointing_attitude",
            version=1,
            min_date_datetime=target_start,
            max_date_datetime=target_end,
            ingestion_date=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    mock_db_session.commit()

    materializations = spacecraft_l1a_job.find_outputs(
        context,
        mock_db_session,
        start_date=target_start,
    )

    assert len(materializations) == 1
    assert materializations[0].metadata["file_names"] == [
        "imap_dps_2026_002_2026_002_01.ah.bc"
    ]


def test_spacecraft_l1a_find_outputs_rejects_overlapping_wrong_day(
    mock_db_session, ephemeral_instance
):
    """A kernel that merely overlaps the partition window must not match.

    Regression test: find_outputs() used to match on any date-range overlap,
    which could pick up a pointing_attitude kernel produced by a different
    (e.g. adjacent or later) run that just happens to touch the edge of this
    partition's window. Since try_to_submit_job() only ever gives the batch
    job a date (no time), the real signal is whether the kernel's coverage
    starts/ends on the same *day* as the partition -- not merely overlaps it.
    """
    context = build_asset_context(
        partition_key="pointingattitude_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

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

    target_start = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)

    # This kernel overlaps 2026-01-02, but starts the day before and ends the
    # day after -- neither its start nor its end lands on the partition's day.
    mock_db_session.add(
        models.SPICEFiles(
            file_path="imap/spice/ck/imap_dps_2026_001_2026_003_01.ah.bc",
            file_name="imap_dps_2026_001_2026_003_01.ah.bc",
            file_root="imap_dps_2026_001_2026_003_.ah.bc",
            kernel_type="pointing_attitude",
            version=1,
            min_date_datetime=datetime.datetime(
                2026, 1, 1, 23, 0, tzinfo=datetime.timezone.utc
            ),
            max_date_datetime=datetime.datetime(
                2026, 1, 3, 1, 0, tzinfo=datetime.timezone.utc
            ),
            ingestion_date=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    mock_db_session.commit()

    materializations = spacecraft_l1a_job.find_outputs(
        context,
        mock_db_session,
        start_date=target_start,
    )

    assert materializations == []


def test_spacecraft_l1a_find_outputs_matches_correct_version(
    mock_db_session, ephemeral_instance
):
    """When a target minor version is known, the kernel version must match it.

    Regression test: two kernels can legitimately share the same day range
    (e.g. a reprocessing attempt), so the version number -- stamped onto the
    output kernel by the processing code to match the requested minor
    version -- is what disambiguates which one this specific run produced.
    """
    context = build_asset_context(
        partition_key="pointingattitude_2026-01-02T00:00:00_to_2026-01-02T23:59:59",
        instance=ephemeral_instance,
    )

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

    target_start = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    target_end = datetime.datetime(2026, 1, 2, 23, 59, 59, tzinfo=datetime.timezone.utc)

    for version, filename in (
        (1, "imap_dps_2026_002_2026_002_01.ah.bc"),
        (2, "imap_dps_2026_002_2026_002_02.ah.bc"),
    ):
        mock_db_session.add(
            models.SPICEFiles(
                file_path=f"imap/spice/ck/{filename}",
                file_name=filename,
                file_root="imap_dps_2026_002_2026_002_.ah.bc",
                kernel_type="pointing_attitude",
                version=version,
                min_date_datetime=target_start,
                max_date_datetime=target_end,
                ingestion_date=datetime.datetime.now(datetime.timezone.utc),
            )
        )
    mock_db_session.commit()

    materializations = spacecraft_l1a_job.find_outputs(
        context,
        mock_db_session,
        output_versions={"pointing-attitude": {"minor_version": 2, "major_version": 1}},
        start_date=target_start,
    )

    assert len(materializations) == 1
    assert materializations[0].metadata["file_names"] == [
        "imap_dps_2026_002_2026_002_02.ah.bc"
    ]


def test_spacecraft_l1a_determine_output_versions_uses_last_attitude_history(
    mock_db_session, ephemeral_instance
):
    """minor_version must come from the *last* attitude_history file used.

    Regression test: _determine_output_versions() must read the version off
    dependency_inputs -- the exact file list resolved for this run -- rather
    than independently re-querying models.SPICEFiles (which could pick a
    different, e.g. superseded, kernel than the one this run actually used).
    Two attitude_history files are included here in a specific order to
    confirm the *last* one (not the first, and not some DB-wide max) wins.
    """
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

    dependency_inputs = processing_input.ProcessingInputCollection(
        processing_input.SPICEInput(
            "naif0012.tls",
            "imap_sclk_0189.tsc",
            "imap_2026_001_2026_011_001.ah.bc",
            "imap_2026_001_2026_100_002.ah.bc",
        )
    )

    output_versions = spacecraft_l1a_job._determine_output_versions(
        session=mock_db_session,
        start_date=datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc),
        dependency_inputs=dependency_inputs,
    )

    assert output_versions == {
        "pointing-attitude": {"minor_version": 2, "major_version": 1}
    }


def test_spacecraft_l1a_determine_output_versions_requires_attitude_history(
    mock_db_session, ephemeral_instance
):
    """No attitude_history file resolved -> raise rather than guess a version."""
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

    dependency_inputs = processing_input.ProcessingInputCollection(
        processing_input.SPICEInput("naif0012.tls", "imap_sclk_0189.tsc")
    )

    with pytest.raises(ValueError, match="attitude_history"):
        spacecraft_l1a_job._determine_output_versions(
            session=mock_db_session,
            start_date=datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc),
            dependency_inputs=dependency_inputs,
        )


def test_spacecraft_l1a_sensor_retriggers_on_contained_attitude_history_kernel(
    mock_db_session, ephemeral_instance
):
    """A new ah kernel contained within an existing partition must retrigger it.

    Regression test for the "contained kernel" gap: _select_maximal_ah_kernels
    discards a fully-contained new ah kernel as already covered, so
    add_pointing_attitude_partitions computes the same partition name as
    before and makes no add/delete. Only the kickoff sensor's phase 2 (the
    generic growing-kernel re-trigger logic) can notice the new kernel and
    re-fire the existing partition.
    """
    spacecraft_l1a_sensor = defs.get_sensor_def(
        "spacecraft_l1a_pointingattitude_kickoff_sensor"
    )

    # Tick 1: baseline. Phase 1 fires exactly one RunRequest for the single
    # existing partition; record the cursor for tick 2.
    context_1 = build_sensor_context(instance=ephemeral_instance)
    run_requests_1 = list(spacecraft_l1a_sensor(context_1))
    assert len(run_requests_1) == 1
    existing_partition = run_requests_1[0].partition_key

    # A new ah kernel, fully CONTAINED within the original kernel's
    # [2026-01-01, 2026-01-11] coverage but with a DIFFERENT min_date_datetime
    # (2026-01-05) -- so _select_maximal_ah_kernels drops it as already
    # covered (add_pointing_attitude_partitions makes no partition changes),
    # while get_growing_kernel_trigger_ranges' predecessor lookup (exact
    # min_date_datetime match) finds none and falls into the Case-3
    # "rollover" fallback, returning this kernel's own contained range.
    mock_db_session.add(
        models.SPICEFiles(
            file_path="imap/spice/imap_2026_005_2026_009_001.ah.bc",
            file_name="imap_2026_005_2026_009_001.ah.bc",
            kernel_type="attitude_history",
            version=1,
            min_date_datetime=datetime.datetime(
                2026, 1, 5, tzinfo=datetime.timezone.utc
            ),
            max_date_datetime=datetime.datetime(
                2026, 1, 9, tzinfo=datetime.timezone.utc
            ),
            ingestion_date=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    mock_db_session.commit()

    # Confirm the partition-maintenance sensor really does nothing: this
    # locks in the premise of the bug (no rename/recreate should occur).
    partitions_before = set(
        ephemeral_instance.get_dynamic_partitions("pointing_attitude_partitions")
    )
    maintenance_context = build_sensor_context(instance=ephemeral_instance)
    maintenance_result = defs.get_sensor_def("add_pointing_attitude_partitions")(
        maintenance_context
    )
    assert list(maintenance_result.dynamic_partitions_requests) == []
    assert (
        set(ephemeral_instance.get_dynamic_partitions("pointing_attitude_partitions"))
        == partitions_before
    )

    # Tick 2: the kickoff sensor must re-trigger the SAME existing partition.
    context_2 = build_sensor_context(
        instance=ephemeral_instance, cursor=context_1.cursor
    )
    run_requests_2 = list(spacecraft_l1a_sensor(context_2))

    assert len(run_requests_2) == 1
    assert run_requests_2[0].partition_key == existing_partition


def test_spacecraft_l1a_sensor_migrates_legacy_list_cursor(
    mock_db_session, ephemeral_instance
):
    """A pre-existing bare-list cursor (the old format) must not crash phase 2."""
    spacecraft_l1a_sensor = defs.get_sensor_def(
        "spacecraft_l1a_pointingattitude_kickoff_sensor"
    )
    existing_partitions = list(
        ephemeral_instance.get_dynamic_partitions("pointing_attitude_partitions")
    )
    legacy_cursor = json.dumps(existing_partitions)

    context = build_sensor_context(instance=ephemeral_instance, cursor=legacy_cursor)
    run_requests = list(spacecraft_l1a_sensor(context))

    # No new partitions and no new attitude_history data -> no RunRequests.
    assert run_requests == []
