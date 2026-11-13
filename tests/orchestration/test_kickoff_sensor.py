"""Tests for the kickoff sensor: partition matching and rerun decisions.

A partition is done only when this job has a successful run for it and at least
one of its outputs was materialized there. A run that skipped for missing
dependencies also ends in SUCCESS, so without the materialization check a
non-triggering input could never rerun a partition that only skipped.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from dagster import (
    AssetKey,
    AssetMaterialization,
    DagsterInstance,
    build_sensor_context,
    instance_for_test,
)

from sds_data_manager.orchestration import dagster_utilities
from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.imap_dagster import (
    defs,
    dependency_config,
    job_handlers,
)
from sds_data_manager.orchestration.imap_job import IMAPJobHandler

REPOINT_416 = "repoint416_2026-10-29T10:03:26_to_2026-10-30T10:03:28"
REPOINT_417 = "repoint417_2026-10-30T10:03:28_to_2026-10-31T10:03:22"
PARTITION = "20260930_20260930T000000_to_20261001T000000"
OTHER_PARTITION = "20261001_20261001T000000_to_20261002T000000"


def _lo_l1a_job():
    return next(
        job
        for job in job_handlers
        if job.dagster_job_name == "lo_l1a_all_processing_job"
    )


def test_adjacent_repoint_partitions_do_not_overlap():
    """A shared endpoint must not map repoint 417's data to repoint 416."""
    with instance_for_test() as instance:
        instance.add_dynamic_partitions("repoint_partitions", [REPOINT_416])
        up_start, up_end = parse_dates_from_partition_key(REPOINT_417)

        target_partitions = _lo_l1a_job()._get_overlapping_target_partitions(
            REPOINT_417, up_start, up_end, instance
        )

    assert target_partitions == []


def test_kickoff_sensor_retries_until_matching_repoint_is_registered():
    """Keep the input event available until its exact target partition exists."""
    with instance_for_test() as instance:
        instance.add_dynamic_partitions("repoint_partitions", [REPOINT_416])
        instance.report_runless_asset_event(
            asset_event=AssetMaterialization(
                asset_key=AssetKey("lo_l0_raw"),
                partition=REPOINT_417,
                metadata={
                    "file_names": ["imap_lo_l0_raw_20261030-repoint00417_v001.pkts"],
                    "input_type": "science",
                },
            )
        )

        kickoff_sensor = defs.get_sensor_def("lo_l1a_all_kickoff_sensor")
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []
        with patch(
            "sds_data_manager.orchestration.imap_job.db.Session"
        ) as session_factory:
            session_factory.return_value.__enter__.return_value = mock_session
            session_factory.return_value.__exit__.return_value = False

            first_context = build_sensor_context(instance=instance)
            first_requests = list(kickoff_sensor(first_context))

            assert first_requests == []
            assert json.loads(first_context.cursor)["lo_l0_raw"] == 0

            instance.add_dynamic_partitions("repoint_partitions", [REPOINT_417])
            second_context = build_sensor_context(
                instance=instance, cursor=first_context.cursor
            )
            second_requests = list(kickoff_sensor(second_context))

    assert [request.partition_key for request in second_requests] == [REPOINT_417]


# Rerun decisions


@pytest.fixture
def job_handler():
    """Return the MAG L1D job, which spin triggers but predicted ephemeris doesn't."""
    return IMAPJobHandler(dependency_config._config[("mag", "l1d", "norm-srf")])


def _input(job_handler, dagster_name):
    """Return the job's input with the given Dagster name."""
    return next(
        dep
        for dep in job_handler.job_config.inputs
        if dep.to_dagster_name() == dagster_name
    )


@pytest.fixture
def non_triggering_input(job_handler):
    """Return an input that doesn't always cause reprocessing."""
    dep = _input(job_handler, "ephemeris_predicted_spice_historical")
    assert dep.to_dagster_name() not in job_handler.triggering_input_names
    return dep


@pytest.fixture
def triggering_input(job_handler):
    """Return an input that always causes reprocessing."""
    dep = _input(job_handler, "spin_spin_historical")
    assert dep.to_dagster_name() in job_handler.triggering_input_names
    return dep


def _materialize(instance, output, partition):
    """Record a materialization of one of the job's outputs."""
    instance.report_runless_asset_event(
        AssetMaterialization(asset_key=output.to_dagster_asset(), partition=partition)
    )


def _run_requests(job_handler, instance, dependency, successful_run):
    """Return the partitions the sensor would request for one new input."""
    context = build_sensor_context(instance=instance)
    runs = [MagicMock()] if successful_run else []
    with patch.object(instance, "get_runs", return_value=runs) as mock_get_runs:
        requests = list(
            job_handler._yield_run_requests_for_partitions(
                context, [PARTITION], dependency, "suffix"
            )
        )
    return [request.partition_key for request in requests], mock_get_runs


@pytest.mark.parametrize(
    ("successful_run", "materialized", "expected"),
    [
        # Never run: requested
        (False, False, [PARTITION]),
        # Ran but only skipped (no output): requested
        (True, False, [PARTITION]),
        # Ran and produced output: not requested
        (True, True, []),
        # Output exists but no SUCCESS run of this job (e.g. reprocessing): requested
        (False, True, [PARTITION]),
    ],
)
def test_non_triggering_input(
    job_handler, non_triggering_input, successful_run, materialized, expected
):
    """A non-triggering input reruns a partition unless it already has output."""
    instance = DagsterInstance.ephemeral()
    if materialized:
        _materialize(instance, job_handler.job_config.outputs[0], PARTITION)

    requested, _ = _run_requests(
        job_handler, instance, non_triggering_input, successful_run
    )
    assert requested == expected


def test_triggering_input_always_requests(job_handler, triggering_input):
    """A triggering input reruns a done partition without checking it."""
    instance = DagsterInstance.ephemeral()
    _materialize(instance, job_handler.job_config.outputs[0], PARTITION)

    requested, mock_get_runs = _run_requests(
        job_handler, instance, triggering_input, successful_run=True
    )
    assert requested == [PARTITION]
    mock_get_runs.assert_not_called()


def test_partition_materialized_any_output(job_handler):
    """Any one of the job's outputs being materialized counts."""
    instance = DagsterInstance.ephemeral()
    outputs = job_handler.job_config.outputs
    _materialize(instance, outputs[-1], PARTITION)

    assert dagster_utilities.partition_materialized(instance, outputs, PARTITION)


def test_partition_materialized_other_partition(job_handler):
    """A materialization of a different partition doesn't count."""
    instance = DagsterInstance.ephemeral()
    outputs = job_handler.job_config.outputs
    _materialize(instance, outputs[0], OTHER_PARTITION)

    assert not dagster_utilities.partition_materialized(instance, outputs, PARTITION)
