"""Test the Hi Goodtimes custom job handler.

Hi Goodtimes needs L1B DE data from the N nearest repoints, not just its own
repoint's DE file. These tests cover the neighbor-repoint gating logic in
sds_data_manager/orchestration/custom_behavior/hi.py, which should:
  - skip (not crash) when a neighboring repoint's DE job is still INPROGRESS
  - skip when it doesn't have enough future neighbors and more pointings could
    still show up
  - proceed when it doesn't have enough future neighbors, but PointingTable
    confirms there is nothing more to wait for
"""

import datetime

import pytest
from dagster import AssetKey, AssetMaterialization, build_asset_context

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import imap_job
from sds_data_manager.orchestration.custom_behavior.hi import HiGoodtimesJob
from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.imap_dagster import job_handlers

TARGET_REPOINT = 3
TARGET_PARTITION = "repoint3_2026-01-03T00:00:00_to_2026-01-03T23:59:59"
FORTYFIVE_SENSOR_GOODTIMES_JOB = "hi_l1b_45sensorgoodtimes_processing_job"
NINETY_SENSOR_GOODTIMES_JOB = "hi_l1b_90sensorgoodtimes_processing_job"


def _hi_goodtimes_job(dagster_job_name: str):
    """Look up the registered Hi Goodtimes job handler by Dagster job name."""
    job = next(
        (j for j in job_handlers if j.dagster_job_name == dagster_job_name),
        None,
    )
    assert job is not None, f"{dagster_job_name} was not found in job_handlers"
    return job


def _repoint_partition(repoint: int) -> str:
    return (
        f"repoint{repoint}_2026-01-{repoint:02d}T00:00:00_"
        f"to_2026-01-{repoint:02d}T23:59:59"
    )


def _materialize(instance, asset_name: str, repoint: int, filename: str):
    """Simulate a science file having been materialized for a given repoint."""
    instance.report_runless_asset_event(
        asset_event=AssetMaterialization(
            asset_key=AssetKey(asset_name),
            partition=_repoint_partition(repoint),
            metadata={
                "file_names": [filename],
                "input_type": "science",
                "version": "v001",
                "start_date": "",
            },
        )
    )


def _de_filename(repoint: int) -> str:
    return f"imap_hi_l1b_45sensor-de_2026010{repoint}-repoint{repoint:05d}_v001.cdf"


def _renamed_de_filename(repoint: int) -> str:
    """Return the `_de_filename` name after HiGoodtimesJob's version renaming.

    HiGoodtimesJob applies the same legacy-version-renaming regex as
    IMAPJobHandler.get_science_files_inputs (see hi.py), which rewrites the
    legacy single-number `vXXX.cdf` suffix into the `vMMM.mmmm.cdf` form.
    """
    return (
        f"imap_hi_l1b_45sensor-de_2026010{repoint}-repoint{repoint:05d}_v001.0001.cdf"
    )


def _materialize_own_pointing_deps(instance, repoint: int):
    """Materialize the diagfee/DE/hk files the base class needs for its own repoint.

    HiGoodtimesJob.get_science_files_inputs() calls super() first, which requires
    all of self.job_config.science_inputs (diagfee, DE, hk) to be found for the
    target repoint before our neighbor-repoint logic ever runs.
    """
    _materialize(
        instance,
        "hi_l1a_45sensordiagfee",
        repoint,
        f"imap_hi_l1a_45sensor-diagfee_2026010{repoint}-repoint{repoint:05d}_v001.cdf",
    )
    _materialize(instance, "hi_l1b_45sensorde", repoint, _de_filename(repoint))
    _materialize(
        instance,
        "hi_l1b_45sensorhk",
        repoint,
        f"imap_hi_l1b_45sensor-hk_2026010{repoint}-repoint{repoint:05d}_v001.cdf",
    )


@pytest.mark.parametrize(
    "dagster_job_name", [FORTYFIVE_SENSOR_GOODTIMES_JOB, NINETY_SENSOR_GOODTIMES_JOB]
)
def test_hi_goodtimes_registered(dagster_job_name):
    """The registry keys must match the YAML descriptors exactly.

    Regression test for a typo ("45-sensorgoodtimes"/"90-sensorgoodtimes" instead
    of "45sensor-goodtimes"/"90sensor-goodtimes") that silently disabled this
    override. Without it, Goodtimes falls back to the generic IMAPJobHandler and
    runs with only its own repoint's DE file, never checking neighbors.
    """
    job = _hi_goodtimes_job(dagster_job_name)
    assert isinstance(job, HiGoodtimesJob)


def test_hi_goodtimes_waits_when_future_pointing_unknown(
    mock_db_session, pointing_table_entries, ephemeral_instance
):
    """Skip when there aren't enough future neighbors and more may still arrive."""
    job = _hi_goodtimes_job(FORTYFIVE_SENSOR_GOODTIMES_JOB)

    _materialize_own_pointing_deps(ephemeral_instance, TARGET_REPOINT)
    # Only past neighbors have DE so far, so none of the nearest repoints found
    # are future ones.
    _materialize(ephemeral_instance, "hi_l1b_45sensorde", 1, _de_filename(1))
    _materialize(ephemeral_instance, "hi_l1b_45sensorde", 2, _de_filename(2))

    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=ephemeral_instance
    )
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    # Pointing 11 (TARGET_REPOINT + 8) was never added to PointingTable (the
    # `pointing_table_entries` fixture only creates 1-10), so we don't yet know
    # whether more data is coming. We should wait rather than proceed.
    with pytest.raises(imap_job.MissingDependenciesError, match="does not exist yet"):
        job.get_science_files_inputs(context, target_start, target_end)


def test_hi_goodtimes_proceeds_when_no_more_data_expected(
    mock_db_session, pointing_table_entries, ephemeral_instance
):
    """Proceed once PointingTable confirms Hi has no more future data to add."""
    job = _hi_goodtimes_job(FORTYFIVE_SENSOR_GOODTIMES_JOB)

    _materialize_own_pointing_deps(ephemeral_instance, TARGET_REPOINT)
    _materialize(ephemeral_instance, "hi_l1b_45sensorde", 1, _de_filename(1))
    _materialize(ephemeral_instance, "hi_l1b_45sensorde", 2, _de_filename(2))

    # Pointing 11 has been downlinked (it exists in PointingTable) with no
    # corresponding Hi L1B DE product -- Hi simply has no more data to add for
    # it, so we shouldn't keep waiting.
    mock_db_session.add(
        models.PointingTable(
            pointing_id=TARGET_REPOINT + 8,
            pointing_start_utc=datetime.datetime(2026, 1, 11, 0, 0, 0),
            pointing_end_utc=datetime.datetime(2026, 1, 11, 23, 59, 59),
        )
    )
    mock_db_session.commit()

    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=ephemeral_instance
    )
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    result = job.get_science_files_inputs(context, target_start, target_end)

    neighbor_files = {
        f
        for science_input in result
        for f in science_input.filename_list
        if "45sensor-de" in f
    }
    assert _renamed_de_filename(1) in neighbor_files
    assert _renamed_de_filename(2) in neighbor_files


def test_hi_goodtimes_waits_for_inprogress_neighbor(
    mock_db_session, ephemeral_instance
):
    """Skip (not crash) when a neighboring repoint's DE job is INPROGRESS.

    Regression test for a bug where the None-check for this case was misplaced
    (checking metadata_list, which is never None, instead of repoint_list),
    which crashed with a TypeError instead of raising MissingDependenciesError.
    """
    job = _hi_goodtimes_job(FORTYFIVE_SENSOR_GOODTIMES_JOB)

    _materialize_own_pointing_deps(ephemeral_instance, TARGET_REPOINT)
    _materialize(ephemeral_instance, "hi_l1b_45sensorde", 1, _de_filename(1))
    mock_db_session.add(
        models.ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument="hi",
            data_level="l1b",
            descriptor="45sensor-de",
            start_date=datetime.datetime(2026, 1, 2),
            major_version=1,
            minor_version=1,
            repointing=2,
            dependency_hash="deadbeef",
            container_command="",
            container_image_digest="sha256:test",
        )
    )
    mock_db_session.commit()

    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=ephemeral_instance
    )
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    with pytest.raises(imap_job.MissingDependenciesError, match="in progress"):
        job.get_science_files_inputs(context, target_start, target_end)
