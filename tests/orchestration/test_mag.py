"""Test the MAG L1C custom job handler.

MAG L1C continues the previous day's L1C timeline across the day boundary
(imap_processing#2925), so its job pulls the previous day's L1C - its own
output product - as an extra input. These tests cover the previous-day
delivery in sds_data_manager/orchestration/custom_behavior/mag.py, which
should:
  - deliver the previous day's L1C alongside the current day's L1B inputs
  - never pick up the current day's own partition (a reprocessing run would
    otherwise be fed its own earlier output)
  - proceed with the current day alone when no previous day L1C exists
  - wait (report a pending dependency) while the previous day's L1C is in
    flight or expected, and stop waiting once it exists, provably has no
    data, or its job already finished
"""

from unittest.mock import PropertyMock, patch

import pytest
from dagster import (
    AssetKey,
    AssetMaterialization,
    DagsterInstance,
    DagsterRun,
    DagsterRunStatus,
    build_asset_context,
)
from dagster._core.remote_origin import (
    RegisteredCodeLocationOrigin,
    RemoteJobOrigin,
    RemoteRepositoryOrigin,
)

from sds_data_manager.orchestration.custom_behavior.mag import (
    FINAL_RETRY_NUMBER,
    MagL1CJob,
)
from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.imap_dagster import job_handlers

TARGET_DAY = 2
TARGET_PARTITION = "daily_2026-01-02T00:00:00_to_2026-01-03T00:00:00"
NORM_MAGO_L1C_JOB = "mag_l1c_normmago_processing_job"
NORM_MAGI_L1C_JOB = "mag_l1c_normmagi_processing_job"


def _mag_l1c_job(dagster_job_name: str):
    """Look up the registered MAG L1C job handler by Dagster job name."""
    job = next(
        (j for j in job_handlers if j.dagster_job_name == dagster_job_name),
        None,
    )
    assert job is not None, f"{dagster_job_name} was not found in job_handlers"
    return job


def _daily_partition(day: int) -> str:
    """Return the day's partition key in the add_daily_partitions format."""
    return f"daily_2026-01-{day:02d}T00:00:00_to_2026-01-{day + 1:02d}T00:00:00"


def _materialize(instance, asset_name: str, day: int, filename: str):
    """Simulate a science file having been materialized for a given day."""
    instance.report_runless_asset_event(
        asset_event=AssetMaterialization(
            asset_key=AssetKey(asset_name),
            partition=_daily_partition(day),
            metadata={
                "file_names": [filename],
                "input_type": "science",
                "version": "v001",
                "start_date": "",
            },
        )
    )


def _l1c_filename(day: int) -> str:
    return f"imap_mag_l1c_norm-mago_2026010{day}_v001.cdf"


def _renamed_l1c_filename(day: int) -> str:
    """Return the `_l1c_filename` name after MagL1CJob's version renaming.

    MagL1CJob applies the same legacy-version-renaming regex as
    IMAPJobHandler.get_science_files_inputs (see mag.py), which rewrites the
    legacy single-number `vXXX.cdf` suffix into the `vMMM.mmmm.cdf` form.
    """
    return f"imap_mag_l1c_norm-mago_2026010{day}_v001.0001.cdf"


def _materialize_current_day_l1b(instance, day: int):
    """Materialize the norm/burst L1B files the base class needs for the day."""
    _materialize(
        instance,
        "mag_l1b_normmago",
        day,
        f"imap_mag_l1b_norm-mago_2026010{day}_v001.cdf",
    )
    _materialize(
        instance,
        "mag_l1b_burstmago",
        day,
        f"imap_mag_l1b_burst-mago_2026010{day}_v001.cdf",
    )


def _science_filenames(science_processing_inputs) -> set:
    return {
        f
        for science_input in science_processing_inputs
        for f in science_input.filename_list
    }


def _add_run(instance, job_name: str, day: int, status, run_id: str, assets=None):
    """Record a run for a given day's partition.

    Sensor-requested runs carry the processing job's name; reprocessing
    backfill runs carry Dagster's implicit asset job name plus an asset
    selection, so `assets` mimics the latter. Dagster refuses to store a
    QUEUED run without the origin its run coordinator would have attached.
    """
    remote_job_origin = None
    if status == DagsterRunStatus.QUEUED:
        remote_job_origin = RemoteJobOrigin(
            repository_origin=RemoteRepositoryOrigin(
                code_location_origin=RegisteredCodeLocationOrigin("test-location"),
                repository_name="test-repo",
            ),
            job_name=job_name,
        )
    instance.run_storage.add_run(
        DagsterRun(
            job_name=job_name,
            run_id=run_id,
            tags={"dagster/partition": _daily_partition(day)},
            status=status,
            asset_selection=assets,
            remote_job_origin=remote_job_origin,
        )
    )


def _pending_context(instance):
    """Build the target day's context.

    The ephemeral_instance fixture registers real daily partitions (the
    add_daily_partitions sensor), so the previous-day keys the pending check
    queries already exist.
    """
    return build_asset_context(partition_key=TARGET_PARTITION, instance=instance)


@pytest.mark.parametrize("dagster_job_name", [NORM_MAGO_L1C_JOB, NORM_MAGI_L1C_JOB])
def test_mag_l1c_registered(dagster_job_name):
    """The registry keys must match the YAML descriptors exactly.

    Without matching keys the L1C jobs silently fall back to the generic
    IMAPJobHandler and never receive the previous day's L1C.
    """
    job = _mag_l1c_job(dagster_job_name)
    assert isinstance(job, MagL1CJob)


def test_mag_l1c_adds_previous_day_l1c(ephemeral_instance):
    """The previous day's L1C is delivered; the current day's own is not."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY)
    _materialize(
        ephemeral_instance,
        "mag_l1c_normmago",
        TARGET_DAY - 1,
        _l1c_filename(TARGET_DAY - 1),
    )
    # The current day's own L1C from an earlier run must not be selected.
    _materialize(
        ephemeral_instance,
        "mag_l1c_normmago",
        TARGET_DAY,
        _l1c_filename(TARGET_DAY),
    )

    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=ephemeral_instance
    )
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    result = job.get_science_files_inputs(context, target_start, target_end)

    filenames = _science_filenames(result)
    assert _renamed_l1c_filename(TARGET_DAY - 1) in filenames
    assert _renamed_l1c_filename(TARGET_DAY) not in filenames
    # The current day's L1B inputs are still delivered.
    assert any("l1b_norm-mago" in f for f in filenames)
    assert any("l1b_burst-mago" in f for f in filenames)


def test_mag_l1c_never_uses_its_own_day(ephemeral_instance):
    """A reprocessing run must not be fed the current day's own earlier L1C."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY)
    # The current day's own L1C already exists (as it would during
    # reprocessing), but no previous day L1C does.
    _materialize(
        ephemeral_instance,
        "mag_l1c_normmago",
        TARGET_DAY,
        _l1c_filename(TARGET_DAY),
    )

    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=ephemeral_instance
    )
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    result = job.get_science_files_inputs(context, target_start, target_end)

    assert not any("l1c" in f for f in _science_filenames(result))


def test_mag_l1c_proceeds_without_previous_day(ephemeral_instance):
    """With no previous day L1C at all, the job runs with the current day alone."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY)

    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=ephemeral_instance
    )
    target_start, target_end = parse_dates_from_partition_key(TARGET_PARTITION)

    result = job.get_science_files_inputs(context, target_start, target_end)

    filenames = _science_filenames(result)
    assert not any("l1c" in f for f in filenames)
    assert any("l1b_norm-mago" in f for f in filenames)


@pytest.mark.parametrize(
    "status",
    [DagsterRunStatus.QUEUED, DagsterRunStatus.STARTING, DagsterRunStatus.STARTED],
)
def test_mag_l1c_waits_while_previous_day_l1c_runs(ephemeral_instance, status):
    """An in-flight run for the previous day's partition means wait."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _add_run(
        ephemeral_instance,
        NORM_MAGO_L1C_JOB,
        TARGET_DAY - 1,
        status,
        "prev-day-in-flight",
    )

    assert job._previous_day_l1c_pending(context) is True


def test_mag_l1c_waits_while_previous_day_backfill_runs(ephemeral_instance):
    """An in-flight reprocessing-backfill run for the previous day also waits."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _add_run(
        ephemeral_instance,
        "__ASSET_JOB",
        TARGET_DAY - 1,
        DagsterRunStatus.STARTED,
        "prev-day-backfill-in-flight",
        assets={AssetKey("mag_l1c_normmago")},
    )

    assert job._previous_day_l1c_pending(context) is True


def test_mag_l1c_waits_for_expected_previous_day_l1c(ephemeral_instance):
    """Previous-day L1Bs exist but no L1C and no finished run: still coming."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY - 1)

    assert job._previous_day_l1c_pending(context) is True


def test_mag_l1c_no_wait_when_previous_day_l1c_exists(ephemeral_instance):
    """An existing previous-day L1C is delivered, not waited on."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY - 1)
    _materialize(
        ephemeral_instance,
        "mag_l1c_normmago",
        TARGET_DAY - 1,
        _l1c_filename(TARGET_DAY - 1),
    )

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_day_before_previous_does_not_satisfy_the_wait(ephemeral_instance):
    """Day N-2's L1C must not stand in for day N-1's.

    One day's partition ends at the exact midnight the next day's begins, so
    an off-by-one window would let day N-2's partition satisfy (or block)
    decisions about day N-1.
    """
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY - 1)
    _materialize(
        ephemeral_instance,
        "mag_l1c_normmago",
        TARGET_DAY - 2,
        _l1c_filename(TARGET_DAY - 2),
    )

    assert job._previous_day_l1c_pending(context) is True


def test_mag_l1c_no_wait_when_previous_day_has_no_data(ephemeral_instance):
    """A previous day with no L1B data has no L1C to wait for."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_no_wait_before_any_partitions_exist(ephemeral_instance):
    """With no daily partitions registered at all, there is nothing to wait for."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    bare_instance = DagsterInstance.ephemeral()
    context = build_asset_context(
        partition_key=TARGET_PARTITION, instance=bare_instance
    )

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_ignores_other_jobs_runs(ephemeral_instance):
    """Another instrument's run on the previous day's partition is not ours."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _add_run(
        ephemeral_instance,
        "codice_l1a_hskp_processing_job",
        TARGET_DAY - 1,
        DagsterRunStatus.STARTED,
        "other-instrument-run",
    )

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_ignores_own_day_run(ephemeral_instance):
    """A run for the current day's own partition is not the previous day's."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _add_run(
        ephemeral_instance,
        NORM_MAGO_L1C_JOB,
        TARGET_DAY,
        DagsterRunStatus.STARTED,
        "own-day-run",
    )

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_no_wait_after_previous_day_job_finished(ephemeral_instance):
    """A finished previous-day run (skip or failure) means no L1C is coming."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY - 1)
    _add_run(
        ephemeral_instance,
        NORM_MAGO_L1C_JOB,
        TARGET_DAY - 1,
        DagsterRunStatus.FAILURE,
        "prev-day-failed",
    )

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_finished_backfill_run_also_counts(ephemeral_instance):
    """A finished reprocessing-backfill run for the previous day also counts.

    Backfill runs carry Dagster's implicit asset job name and an asset
    selection instead of this job's name.
    """
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY - 1)
    _add_run(
        ephemeral_instance,
        "__ASSET_JOB",
        TARGET_DAY - 1,
        DagsterRunStatus.FAILURE,
        "prev-day-backfill-failed",
        assets={AssetKey("mag_l1c_normmago")},
    )

    assert job._previous_day_l1c_pending(context) is False


def test_mag_l1c_stops_waiting_on_final_retry(ephemeral_instance):
    """The wait gives up on the last retry instead of failing the run."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    # The previous day's L1C is expected, so the job would normally wait.
    _materialize_current_day_l1b(ephemeral_instance, TARGET_DAY - 1)

    with patch.object(
        type(context), "retry_number", new_callable=PropertyMock
    ) as retry_number:
        retry_number.return_value = 0
        assert job._check_for_running_dependencies(context) is True
        retry_number.return_value = FINAL_RETRY_NUMBER
        assert job._check_for_running_dependencies(context) is False


def test_mag_l1c_still_waits_on_upstream_ancestors(ephemeral_instance):
    """The base class's ancestor check still applies to MAG L1C runs."""
    job = _mag_l1c_job(NORM_MAGO_L1C_JOB)
    context = _pending_context(ephemeral_instance)

    # An in-flight run materializing an upstream MAG L1B asset, as a
    # reprocessing backfill would submit it.
    _add_run(
        ephemeral_instance,
        "__ASSET_JOB",
        TARGET_DAY,
        DagsterRunStatus.STARTED,
        "upstream-l1b-run",
        assets={AssetKey("mag_l1b_normmago")},
    )

    assert job._check_for_running_dependencies(context) is True
