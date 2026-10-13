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
