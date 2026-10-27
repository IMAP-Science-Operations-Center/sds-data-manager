"""Test for sub submission functionalities."""

from dagster import (
    DagsterEvent,
    DagsterEventType,
    DagsterInstance,
    Definitions,
    build_asset_context,
)

from sds_data_manager.orchestration.imap_dagster import dependency_config
from sds_data_manager.orchestration.imap_job import IMAPJobHandler


def test_check_for_running_dependencies() -> None:
    """Test the reprocessing functionality."""
    instance = DagsterInstance.ephemeral()
    partition_key = "repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"
    instance.add_dynamic_partitions("repoint_partitions", [partition_key])

    job_handler = IMAPJobHandler(dependency_config._config[("glows", "l1a", "all")])
    defs = Definitions(
        assets=[job_handler.build_asset()], sensors=[job_handler.build_sensor()]
    )

    # run_job receives an asset context with the partition it is processing
    context = build_asset_context(instance=instance, partition_key=partition_key)
    # Mock a glows l1a run. The kickoff sensor's RunRequest(partition_key=...)
    # stores the partition on the run as the "dagster/partition" tag.
    run = instance.create_run_for_job(
        job_def=defs.resolve_job_def(job_handler.dagster_job_name),
        tags={"dagster/partition": partition_key},
    )
    # 2. Transition run to status = started.
    instance.report_dagster_event(
        run_id=run.run_id,
        dagster_event=DagsterEvent(
            event_type_value=DagsterEventType.PIPELINE_START.value,
            job_name=run.job_name,
        ),
    )
    job = ("glows", "l1b", "de")
    job_handler = IMAPJobHandler(dependency_config._config[job])
    ancestor_running = job_handler._check_for_running_dependencies(context)
    assert ancestor_running


def test_check_for_running_dependencies_different_partition() -> None:
    """Test the reprocessing functionality runs jobs in different partitions."""
    instance = DagsterInstance.ephemeral()
    partition_key_job1 = "repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"
    partition_key_job2 = "repoint123_2026-01-02T00:00:00_to_2026-01-03T00:00:00"
    partition_key_job3 = "repoint123_2026-01-03T00:00:00_to_2026-01-04T00:00:00"
    instance.add_dynamic_partitions(
        "repoint_partitions",
        [partition_key_job1, partition_key_job2, partition_key_job3],
    )

    job_handler = IMAPJobHandler(dependency_config._config[("glows", "l1a", "all")])
    defs = Definitions(
        assets=[job_handler.build_asset()], sensors=[job_handler.build_sensor()]
    )

    # run_job receives an asset context with the partition it is processing
    context = build_asset_context(instance=instance, partition_key=partition_key_job2)
    # Mock 2 existing glows l1a runs with a partition right before and right after the
    # Current job's partition.
    # The kickoff sensor's RunRequest(partition_key=...) stores the partition on
    # the run as the "dagster/partition" tag.
    for partition in [partition_key_job1, partition_key_job3]:
        run = instance.create_run_for_job(
            job_def=defs.resolve_job_def(job_handler.dagster_job_name),
            tags={"dagster/partition": partition},
        )
        # 2. Transition run to status = started.
        instance.report_dagster_event(
            run_id=run.run_id,
            dagster_event=DagsterEvent(
                event_type_value=DagsterEventType.PIPELINE_START.value,
                job_name=run.job_name,
            ),
        )
    # 3. Check to see if there are running dependencies with the same partition
    # This should return None even though there are multiple upstream dependencies
    # running. They are in different partitions, so the downstream job should be
    # allowed to run.
    job = ("glows", "l1b", "de")
    job_handler = IMAPJobHandler(dependency_config._config[job])
    ancestor_running = job_handler._check_for_running_dependencies(context)
    assert not ancestor_running


def test_check_for_running_dependencies_l1c_psets_to_map() -> None:
    """Test the upstream check will skip l2 map jobs if there are psets running."""
    instance = DagsterInstance.ephemeral()
    pset_partition_key = "repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"
    map_partition_key = "cadence-3mo_2025-12-25T00:00:00_to_2026-03-25T00:00:00"
    # Although map jobs rely on psets, they do not share partitions. PSETs are
    # by repointing and maps are chunked into 3 month, 6 month or 1 year cadences.
    # We need to check that if any pset job is running with a partion that
    # overlaps a map job that is about to run, the map job should wait until
    # the pset is finished processing to run.
    instance.add_dynamic_partitions(
        "repoint_partitions", [pset_partition_key, map_partition_key]
    )

    job_handler = IMAPJobHandler(
        dependency_config._config[("ultra", "l1c", "45sensor-spacecraftpset")]
    )
    defs = Definitions(
        assets=[job_handler.build_asset()], sensors=[job_handler.build_sensor()]
    )

    # run_job receives an asset context with the partition it is processing
    context = build_asset_context(instance=instance, partition_key=map_partition_key)
    # Mock existing ultra pset run with a partition that overlaps the map job's
    # partition. The kickoff sensor's RunRequest(partition_key=...) stores the
    # partition on the run as the "dagster/partition" tag.
    run = instance.create_run_for_job(
        job_def=defs.resolve_job_def(job_handler.dagster_job_name),
        tags={"dagster/partition": pset_partition_key},
    )
    # 2. Transition run to status = started.
    instance.report_dagster_event(
        run_id=run.run_id,
        dagster_event=DagsterEvent(
            event_type_value=DagsterEventType.PIPELINE_START.value,
            job_name=run.job_name,
        ),
    )
    # 3. _check_for_running_dependencies should return true because the pset is
    # running upstream and although the portions are not an exact match,
    # they do overlap.
    job = ("ultra", "l2", "u45-ena-h-sf-nsp-full-hae-2deg-3mo")
    job_handler = IMAPJobHandler(dependency_config._config[job])
    ancestor_running = job_handler._check_for_running_dependencies(context)
    assert ancestor_running

    # 4. Transition run to status = succeeded.
    instance.report_dagster_event(
        run_id=run.run_id,
        dagster_event=DagsterEvent(
            event_type_value=DagsterEventType.RUN_SUCCESS.value,
            job_name=run.job_name,
        ),
    )

    # Check if any upstream jobs are running now. This should return false.
    ancestor_running = job_handler._check_for_running_dependencies(context)
    assert not ancestor_running
