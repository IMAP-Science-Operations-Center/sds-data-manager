"""Override behavior for l2 Map processing."""

import datetime

from dagster import (
    RunRequest,
    SensorEvaluationContext,
    sensor,
)

from sds_data_manager.orchestration import (
    imap_job,
)
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry
from sds_data_manager.orchestration.maps_utils import (
    _CADENCE_TYPES,
    get_map_partition_names,
)

CADENCE_PATTERN = rf"{'|'.join([desc for desc in _CADENCE_TYPES])}"


@JobBuilderRegistry.register_descriptor_pattern("ultra", "l2", CADENCE_PATTERN)
@JobBuilderRegistry.register_descriptor_pattern("hi", "l2", CADENCE_PATTERN)
@JobBuilderRegistry.register_descriptor_pattern("lo", "l2", CADENCE_PATTERN)
class L2MapJob(imap_job.IMAPJobHandler):
    """Overriding parts of the Hi processing pipeline."""

    def __init__(self, job):
        """Initialize the handler, then override the sensor run frequency."""
        super().__init__(job)
        # Run the sensor every week (604800 seconds) to re-run active
        # progressive map partitions.
        self.sensor_run_frequency = 604800

    def build_sensor(self):
        """Return a Dagster sensor monitoring for new cadence partitions.

        Note that this does not perform all dependency checks.
        That job is part of the @asset's job.
        This job simply alerts the asset if there is the *potential* to start.

        1) Check for any new partitions since last sensor tick.
        2) Yield a RunRequest for each new partition key.
        """
        sensor_name = f"{self.job_config.to_dagster_name()}_kickoff_sensor"

        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=self.sensor_run_frequency,
        )
        def _sensor(context: SensorEvaluationContext):
            cadence_str = self.job_config.partition

            # Find all map windows for this job's cadence.
            # Partitions come back oldest-first, so the last one is the open window.
            # This open window partition gets progressively re-processed every tick
            # with new available science data.
            partitions = get_map_partition_names(cadence_str, include_open=True)
            if not partitions:
                context.log.info(f"No active {cadence_str} map partition found.")
                return

            partition_name = partitions[-1]

            # add_cadence_map_partitions registers new partitions once a day, so
            # there can be a lag before Dagster knows about this window. Only
            # trigger runs for partitions that have actually been registered.
            existing_partitions = context.instance.get_dynamic_partitions(
                self.partitions_def.name
            )
            if partition_name not in existing_partitions:
                context.log.info(
                    f"Partition {partition_name} not yet registered, skipping."
                )
                return

            # Create a unique suffix for this sensor trigger
            job_suffix = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            run_key = "_".join(
                [
                    self.job_config.to_dagster_name(),
                    partition_name,
                    job_suffix,
                ]
            )
            context.log.info(
                f"Yielding a run request with ID: {run_key} "
                f"on partition {partition_name}."
            )
            # Kicks off the job by requesting a run in Dagster
            yield RunRequest(run_key=run_key, partition_key=partition_name)

            context.update_cursor(partition_name)

        return _sensor
