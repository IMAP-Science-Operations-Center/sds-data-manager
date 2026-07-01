"""Override behavior for l2 Map processing."""

import json

from dagster import (
    RunRequest,
    SensorEvaluationContext,
    SensorResult,
    SkipReason,
    sensor,
)

from sds_data_manager.orchestration import (
    imap_job,
)
from sds_data_manager.orchestration.custom_partitions import CADENCE_PARTITION_DEFS


# TODO register all map jobs OR in JobBuilderRegistry.get_builder return map jobs if
#   descriptor indicates a map job e.g. "3mo".
class L2MapJob(imap_job.IMAPJobHandler):
    """Overriding parts of the Hi processing pipeline."""

    # TODO do we need to override any other functions?

    # Override the sensor to kickoff jobs not based on upstream files but whether there
    # are new partitions registered by add_cadence_map_partitions sensor.
    def build_sensor(self):
        """Return a Dagster sensor monitoring for new cadence partitions.

        Note that this does not perform all dependency checks.
        That job is part of the @asset's job.
        This job simply alerts the asset if there is the *potential* to start.

        1) Check for any new partitions since last sensor tick.
        2) For any new partitions, check if the job has already been run for that
         partition.
        3) If the job has not been run for that partition, yield a RunRequest
        """
        sensor_name = f"{self.job_config.to_dagster_name()}_kickoff_sensor"

        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=self.sensor_run_frequency,
        )
        def _sensor(context: SensorEvaluationContext):
            cadence_str = self.job_config.descriptor.split("-")[-1]
            partition_def = CADENCE_PARTITION_DEFS[cadence_str]

            # Get the existing partitions for this cadence.
            existing_partitions = set(
                context.instance.get_dynamic_partitions(partition_def.name)
            )

            # Get the partitions read at the last sensor tick.
            seen_partitions = (
                set(json.loads(context.cursor)) if context.cursor else set()
            )
            # Get the new partitions that have been added since the last sensor tick.
            new_partitions = existing_partitions - seen_partitions
            if not new_partitions:
                return SkipReason("No new cadence partitions to process")

            # Submit a run request for each new partition.
            run_requests = [
                RunRequest(run_key=partition_name, partition_key=partition_name)
                for partition_name in sorted(new_partitions)
            ]
            # Update the curser to include the new partitions that have been seen.
            return SensorResult(
                run_requests=run_requests,
                cursor=json.dumps(sorted(seen_partitions | new_partitions)),
            )
