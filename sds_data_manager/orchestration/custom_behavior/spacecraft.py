"""Override behavior for Spacecraft processing."""

import datetime
import json

from dagster import (
    RunRequest,
    SensorEvaluationContext,
    sensor,
)

from sds_data_manager.orchestration import (
    imap_job,
)
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


@JobBuilderRegistry.register("spacecraft", "l1a", "pointing-attitude")
class SpacecraftPointingAttitudeJob(imap_job.IMAPJobHandler):
    """Overriding parts of the spacecraft processing pipeline."""

    def get_science_files_inputs(self, context, target_start, target_end):
        """Override default behavior to return nothing."""
        return []

    def build_sensor(self):
        """Return a Dagster sensor monitoring for new dependencies.

        By running on each new repoint partition, rather than new repoint files,
        we ensure that dagster has been given enough time to actually create the
        new custom partitions.

        """
        sensor_name = f"{self.job_config.to_dagster_name()}_kickoff_sensor"

        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=self.sensor_run_frequency,
        )
        def _sensor(context: SensorEvaluationContext):

            # Create a unique suffix for this sensor trigger
            job_suffix = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor_str = context.cursor or "[]"
            processed_partitions = set(json.loads(cursor_str))

            # Query the instance for the current state of the dynamic partitions
            current_partitions = set(
                context.instance.get_dynamic_partitions(self.partitions_def.name)
            )
            # Determine the delta
            new_partitions = current_partitions - processed_partitions

            # Yield a RunRequest for each new partition
            for partition_key in new_partitions:
                run_key = "_".join(
                    [
                        self.job_config.to_dagster_name(),
                        partition_key,
                        job_suffix,
                    ]
                )
                yield RunRequest(
                    run_key=run_key,
                    partition_key=partition_key,
                )

            # Update the cursor so we don't process these again
            # We store the full current set so the next tick has the latest baseline
            context.update_cursor(json.dumps(list(current_partitions)))

        return _sensor
