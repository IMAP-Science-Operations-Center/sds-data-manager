"""The Dagster entrypoint. Builds all assets and sensors."""

import importlib
import pkgutil

from dagster import Definitions
from imap_data_access import VALID_DATALEVELS

import sds_data_manager.orchestration.custom_behavior
from sds_data_manager.orchestration import (
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

# File handlers that materialize via the single consolidated sensor below, vs.
# those (e.g. IDEX) that fully manage their own materialization and sensor.
default_file_handlers = [h for h in file_handlers if h.USE_COMMON_SENSOR]
custom_file_handlers = [h for h in file_handlers if not h.USE_COMMON_SENSOR]

# Every output a job or default file handler could materialize, along with the
# partitions_def to use for it, fed into the single materialization sensor.
materialization_targets = []
for job in job_handlers:
    for output in job.job_config.outputs:
        materialization_targets.append((output, job.partitions_def))
for handler in default_file_handlers:
    materialization_targets.append((handler.job_config, handler.partitions_def))

# These sensors determine when it is time to kick off a job
kickoff_sensors = [job.build_sensor() for job in job_handlers]
# These sensors have custom behavior for materializing assets that are not handled
# by the consolidated sensor
custom_sensors = [handler.build_sensor() for handler in custom_file_handlers]
# This sensor materializes all assets that are handled by the consolidated file sensor
new_files_sensor = build_materialization_sensor(materialization_targets)
sensors = kickoff_sensors + custom_sensors + [new_files_sensor]

batch_jobs = [asset.build_asset() for asset in assets_to_build]

assets = batch_jobs

defs = Definitions(
    assets=assets, sensors=custom_partitions.sensors + sensors + reprocessing.sensors
)
