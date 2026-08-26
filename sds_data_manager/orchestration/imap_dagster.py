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

sensors = []
batch_jobs = []
for asset in assets_to_build:
    batch_jobs.append(asset.build_asset())
    sensors.append(asset.build_sensor())

assets = batch_jobs

defs = Definitions(
    assets=assets, sensors=custom_partitions.sensors + sensors + reprocessing.sensors
)
