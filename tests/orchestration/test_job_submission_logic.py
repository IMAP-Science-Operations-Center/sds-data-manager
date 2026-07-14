"""Test for sub submission functionalities."""

from dagster import (
    AssetKey,
    DagsterEvent,
    DagsterEventType,
    DagsterInstance,
    Definitions,
    asset,
    build_sensor_context,
)

from sds_data_manager.orchestration.custom_partitions import (
    repoint_partitions,
)
from sds_data_manager.orchestration.imap_dagster import dependency_config
from sds_data_manager.orchestration.imap_job import IMAPJobHandler


def test_check_for_running_dependencies() -> None:
    """Verify _check_for_running_dependencies detects in-flight upstream asset runs."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "repoint_partitions", ["repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"]
    )

    # Set up upstream assets for glows pipeline
    @asset(partitions_def=repoint_partitions)
    def glows_l1a_hist():
        pass

    @asset(partitions_def=repoint_partitions, deps=[glows_l1a_hist])
    def glows_l1b_hist():
        pass

    @asset(partitions_def=repoint_partitions, deps=[glows_l1b_hist])
    def glows_l2_hist():
        pass

    defs = Definitions(assets=[glows_l1a_hist, glows_l1b_hist, glows_l2_hist])
    repo_def = defs.get_repository_def()

    context = build_sensor_context(
        instance=instance,
        repository_def=repo_def,
    )

    # Mock a glows l1a run
    run = instance.create_run_for_job(
        job_def=defs.resolve_job_def("__ASSET_JOB"),
        asset_selection={AssetKey("glows_l1a_hist")},
    )
    # 2. Transition run to status = started.
    instance.report_dagster_event(
        run_id=run.run_id,
        dagster_event=DagsterEvent(
            event_type_value=DagsterEventType.PIPELINE_START.value,
            job_name=run.job_name,
        ),
    )
    # This job is 2 levels downstream from the l1a hist job.
    job = ("glows", "l2", "hist")
    job_handler = IMAPJobHandler(dependency_config._config[job])
    ancestor_running = job_handler._check_for_running_dependencies(context)
    assert ancestor_running
