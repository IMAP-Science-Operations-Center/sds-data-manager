"""Override behavior for l2 Map processing."""

import datetime
import json

from dagster import (
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    sensor,
)

from sds_data_manager.orchestration import (
    imap_job,
)
from sds_data_manager.orchestration.custom_partitions import CADENCE_PARTITION_DEFS
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry
from sds_data_manager.orchestration.maps_utils import _CADENCE_TYPES

CADENCE_PATTERN = rf"{'|'.join([desc for desc in _CADENCE_TYPES])}"


@JobBuilderRegistry.register_descriptor_pattern("ultra", "l2", CADENCE_PATTERN)
@JobBuilderRegistry.register_descriptor_pattern("hi", "l2", CADENCE_PATTERN)
@JobBuilderRegistry.register_descriptor_pattern("lo", "l2", CADENCE_PATTERN)
class L2MapJob(imap_job.IMAPJobHandler):
    """Overriding parts of the Hi processing pipeline."""

    # TODO do we need to override any other functions?

    def __init__(self, job):
        """Initialize the handler, then override the sensor run frequency."""
        super().__init__(job)
        self.sensor_run_frequency = 43200  # Run the sensor every 12 hours

    # Override the sensor to kickoff jobs not based on upstream files but whether there
    # are new partitions registered by add_cadence_map_partitions sensor.
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
            # TODO should we wait longer for maps?
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
            context.log.info(
                "Cadence sensor state for %s: existing=%d, seen=%d, new=%d",
                self.job_config.to_dagster_name(),
                len(existing_partitions),
                len(seen_partitions),
                len(new_partitions),
            )
            if not new_partitions:
                yield SkipReason("No new cadence partitions to process")
                return

            for partition_name in sorted(new_partitions):
                # Create a unique suffix for this sensor trigger
                job_suffix = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                run_key = "_".join(
                    [
                        self.job_config.to_dagster_name(),
                        partition_name,
                        job_suffix,
                    ]
                )
                yield RunRequest(run_key=run_key, partition_key=partition_name)

            context.update_cursor(json.dumps(sorted(seen_partitions | new_partitions)))

        return _sensor
