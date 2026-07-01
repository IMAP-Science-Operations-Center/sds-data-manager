"""Override behavior for Map processing."""

from dagster import (
    SensorEvaluationContext,
    sensor,
)

from sds_data_manager.orchestration import (
    imap_job,
)


# TODO register all map jobs OR in JobBuilderRegistry.get_builder return map jobs if
#   descriptor indicates a map job e.g. "3mo".
class MapJob(imap_job.IMAPJobHandler):
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

            # Create a unique suffix for this sensor trigger
            # job_suffix = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # TODO build curser that keeps tracks of which cadence partitions
            #   exist. Get current partitions for the cadence. Compare against curser
            #   and see if any were added. If so, loop through each new partition
            #   and yield a run request.
            pass
