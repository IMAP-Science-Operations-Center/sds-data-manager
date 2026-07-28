"""Test the MAG L1C custom job handler.

MAG L1C continues the previous day's L1C timeline across the day boundary
(imap_processing#3323), so its job pulls the previous day's L1C - its own
output product - as an extra input. These tests cover the previous-day
delivery in sds_data_manager/orchestration/custom_behavior/mag.py, which
should:
  - deliver the previous day's L1C alongside the current day's L1B inputs
  - never pick up the current day's own partition (a reprocessing run would
    otherwise be fed its own earlier output)
  - proceed with the current day alone when no previous day L1C exists
"""

import pytest
from dagster import AssetKey, AssetMaterialization, build_asset_context

from sds_data_manager.orchestration.custom_behavior.mag import MagL1CJob
from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.imap_dagster import job_handlers

TARGET_DAY = 2
TARGET_PARTITION = "daily_2026-01-02T00:00:00_to_2026-01-02T23:59:59"
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
    return f"daily_2026-01-{day:02d}T00:00:00_to_2026-01-{day:02d}T23:59:59"


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
