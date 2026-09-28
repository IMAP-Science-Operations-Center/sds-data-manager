"""The Dagster entrypoint. Builds all assets and sensors."""

import importlib
import pkgutil

from dagster import Definitions
from imap_data_access import VALID_DATALEVELS

import sds_data_manager.orchestration.custom_behavior
from sds_data_manager.orchestration import (
    config,
    custom_partitions,
    reprocessing,
)
from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
)
from sds_data_manager.orchestration.file_handler_registry import FileBuilderRegistry
from sds_data_manager.orchestration.imap_file import build_materialization_sensor
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


# This ensures that the custom behavior is loaded in appropriately before called
def load_all_builders():
    """Dynamically imports all modules in the builders package to trigger decorators."""
    package = sds_data_manager.orchestration.custom_behavior
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{module_name}")


load_all_builders()


dependency_config = DependencyConfigReader()

file_handlers = []
job_handlers = []

# Each key in _config is a downstream job (source, data_type, descriptor).
# Bucket each job into the right handler list based on its data_type.
all_jobs = dependency_config._config.keys()
unique_job_names = []

# First, we're going to loop through first to find all job outputs
all_outputs = []
for potential_job in all_jobs:
    outputs_list = list(dependency_config.outputs(potential_job))
    for output in outputs_list:
        name = output.to_dagster_name()
        all_outputs.append(name)

# Next, we'll gather up all the job and file handlers
for potential_job in all_jobs:
    partition = dependency_config.partition(potential_job)
    inputs_list = list(dependency_config.inputs(potential_job))
    source, data_type, descriptor = potential_job

    if data_type in VALID_DATALEVELS:
        job = JobBuilderRegistry.get_builder(dependency_config._config[potential_job])

        if job.job_config.to_dagster_name() not in unique_job_names:
            job_handlers.append(job)
            unique_job_names.append(job.job_config.to_dagster_name())

        # Finally, check for inputs that do not have a corresponding output.
        for input in inputs_list:
            input_name = input.to_dagster_name()
            if (input_name not in all_outputs) and (input_name not in unique_job_names):
                if "_ancillary_" in input_name:
                    continue
                elif "spice" in input_name:
                    continue
                elif "spin" in input_name:
                    continue
                elif "repoint" in input_name:
                    continue
                file_handler = FileBuilderRegistry.get_builder(
                    input, job.partitions_def
                )
                file_handlers.append(file_handler)
                unique_job_names.append(input_name)

# store in assets list
assets_to_build = job_handlers + file_handlers

# File handlers that materialize via the shared file sensor below, vs.
# those (e.g. IDEX) that fully manage their own materialization and sensor.
default_file_handlers = [h for h in file_handlers if h.USE_COMMON_SENSOR]
custom_file_handlers = [h for h in file_handlers if not h.USE_COMMON_SENSOR]

# File-only assets have nothing else materializing them, so this sensor is their
# primary materialization path. It only waits long enough for the indexer to commit.
new_files_sensor = build_materialization_sensor(
    [(handler.job_config, handler.partitions_def) for handler in default_file_handlers],
    name="science_file_materialization_sensor",
    materialized_by="file_sensor",
    min_age=config.FILE_MATERIALIZATION_MIN_AGE,
)

# Processing job outputs are materialized by their job's op. This backup sensor
# only picks up outputs the op missed (e.g. the run was interrupted after the Batch
# job succeeded), and waits long enough that it doesn't race the op.
job_output_backup_sensor = build_materialization_sensor(
    [
        (output, job.partitions_def)
        for job in job_handlers
        for output in job.job_config.outputs
    ],
    name="job_output_backup_materialization_sensor",
    materialized_by="backup_sensor",
    min_age=config.BACKUP_MATERIALIZATION_MIN_AGE,
    is_backup=True,
)

# These sensors determine when it is time to kick off a job
kickoff_sensors = [job.build_sensor() for job in job_handlers]

# These sensors have custom behavior for materializing assets that are not handled
# by the shared file sensor (at the moment, just IDEX)
custom_sensors = [handler.build_sensor() for handler in custom_file_handlers]

# Combine all sensors
sensors = (
    kickoff_sensors + custom_sensors + [new_files_sensor, job_output_backup_sensor]
)

# Combine all jobs
batch_jobs = [asset.build_asset() for asset in assets_to_build]

defs = Definitions(
    assets=batch_jobs,
    sensors=custom_partitions.sensors + sensors + reprocessing.sensors,
)
