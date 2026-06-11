"""IMAP job handler for managing L3 file creation from Menlo."""

import datetime
import hashlib
import json

from dagster import (
    RunRequest,
    SensorEvaluationContext,
    sensor,
)

from sds_data_manager.orchestration import (
    dagster_utilities,
)
from sds_data_manager.orchestration import imap_job

class L3CronHandler(imap_job.IMAPJobHandler):
    """Handle IMAP job dependencies and submission."""

    def build_sensor(self):
        """"""
        sensor_name = f"{self.job_config.to_dagster_name()}_kickoff_sensor"

        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=43200,
        )
        def _sensor(context: SensorEvaluationContext):

            # Create a unique suffix for this sensor trigger
            job_suffix = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Get the affected partition as it is *today* 
            affected_partitions = dagster_utilities.get_affected_partitions(context,
                                                      self.partitions_def,
                                                      min_dt=datetime.datetime.now(),
                                                      max_dt=datetime.datetime.now())
            target_partition = affected_partitions[0]
            
            run_key = "_".join([self.job_config.to_dagster_name(), 
                                target_partition, 
                                job_suffix
                                ]
                                )
            context.log.info(
                f"""Yielding a run request with ID:
                    {run_key} on partition {target_partition}.
                    """
            )

            # Go to _generic_batch_sumbitter
            yield RunRequest(partition_key=target_partition, run_key=run_key)

        return _sensor

    def _dependency_hash(self, serialized_dependencies: str):
        """Generate a hash for the serialized dependencies.

        We are overriding the behavior of IMAPJobHandler. Dependencies are largely
        handled internally in the L3 code. Instead, we append the current date of 
        YYYYMMDD to the dependency hash, ensuring that a particular file is not 
        generated with the same code more than once per day. 

        Parameters
        ----------
        serialized_dependencies : str
            The serialized dependencies string.

        Returns
        -------
        str
            The first 8 characters of the SHA-256 hash of the serialized dependencies.
        """
        # We need to pull out the individual files and put them in alphabetical order
        dependencies = json.loads(serialized_dependencies)
        non_sclk_deps = []
        for dep in dependencies:
            for file in dep["files"]:
                if "imap_sclk" not in file and ".repoint" not in file:
                    # We'll get rid of the spacecraft_clock kernel and repoint file.
                    # These are updated frequently, and make zero
                    # difference to processing.
                    non_sclk_deps.append(file)
        # Append the image_digest
        sorted_files = sorted(list(set(non_sclk_deps)))
        sorted_files.append(self._get_container_image_digest())
        sorted_files.append(datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"))
        joined_string = "|".join(sorted_files)

        return hashlib.sha256(joined_string.encode("utf-8")).hexdigest()[:8]
