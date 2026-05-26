from dagster import Definitions
from imap_data_access import VALID_DATALEVELS
from sds_data_manager.orchestration import custom_partitions, repoint_file, spice, idex
from sds_data_manager.orchestration import spin
from sds_data_manager.orchestration.imap_file import IMAPAncillaryFileHandler, IMAPScienceFileHandler
from sds_data_manager.orchestration.imap_job import IMAPJobHandler
from sds_data_manager.orchestration.dependency_refactoring.dependency import (
    DependencyConfigReader,
)

# TODO: how to push code changes to dagster cluster?
# Potential solution: after CDK deploy to dev or prod, add Git action to deploy latest code
# to dagster using this command:
#   aws ecs update-service --cluster DagsterEcsStack-DagsterClusterxxx --service DagsterEcsStack-DagsterWebserverServicexxx
#   --force-new-deployment --profile xxx --region us-west-2
dependency_config = DependencyConfigReader()

ancillary_handlers = []
l0_job_handlers = []
non_l0_job_handlers = []

# Each key in _config is a downstream job (source, data_type, descriptor).
# Bucket each job into the right handler list based on its data_type.
all_jobs = dependency_config._config.keys()
all_job_inputs = set()
all_job_outputs = []

for potential_job in all_jobs:
    partition = dependency_config.partition(potential_job)
    inputs_list = list(dependency_config.inputs(potential_job))
    outputs_list = list(dependency_config.outputs(potential_job))
    
    inputs_list_2 = []
    for input in inputs_list:
        name = input.source + '_' + input.data_type + '_' + input.descriptor
        inputs_list_2.append((name, partition))
    outputs_list_2 = []
    for output in outputs_list:
        name = output.source + '_' + output.data_type + '_' + output.descriptor
        outputs_list_2.append(name)
    all_job_inputs.update(inputs_list_2)
    all_job_outputs.extend(outputs_list_2)
    
    source, data_type, descriptor = potential_job
    asset_name = f"{source}_{data_type}_{descriptor}"

    if data_type in VALID_DATALEVELS:
        if ('3mo' not in descriptor) and ('6mo' not in descriptor) and ('1yr' not in descriptor): # Skip maps for now
            non_l0_job_handlers.append(IMAPJobHandler(asset_name, partition, inputs_list, outputs_list))

# Check for any inputs that have no outputs
assets_created = []
for input_name, partition in all_job_inputs:
    if ('3mo' not in input_name) and ('6mo' not in input_name) and ('1yr' not in input_name): # Skip maps for now
        if (input_name not in all_job_outputs) and (input_name not in assets_created):
            if "_ancillary_" in input_name:
                ancillary_handlers.append(IMAPAncillaryFileHandler(input_name))
                assets_created.append(input_name)
            elif "idex_l0_" in input_name:
                # Continue because IDEX will defined custom asset and sensor below.
                continue
            elif "spice" in input_name:
                continue
            elif "spin_spin" in input_name:
                continue
            elif "repoint_repoint" in input_name:
                continue
            else:
                l0_job_handlers.append(IMAPScienceFileHandler(input_name, partition))
                assets_created.append(input_name)

# Now using handlers, create assets for each handler:
#   1. create asset using handler.build_asset()
#   2. create assets for spice, spin, and a repoint file
# store in assets list
assets_to_build = ancillary_handlers + l0_job_handlers + non_l0_job_handlers

sensors = []
batch_jobs = []
spice_jobs = []
spin_jobs = []
repoint_file_jobs = []
repoint_jobs_created = set()
spin_jobs_created = set()
spice_jobs_created = set()
for asset in assets_to_build:
    batch_jobs.append(asset.build_asset())
    sensors.append(asset.build_sensor())
    if asset.spice_dependency_name and asset.spice_dependency_name not in spice_jobs_created:
        spice_jobs.append(spice.build_spice_deps_asset(asset.spice_dependency_name, asset.partitions_def, asset.spice_types))
        spice_jobs_created.add(asset.spice_dependency_name)
    if asset.spin_dependency_name and asset.spin_dependency_name not in spin_jobs_created:
        spin_jobs.append(spin.build_spin_deps_asset(asset.spin_dependency_name, asset.partitions_def))
        spin_jobs_created.add(asset.spin_dependency_name)
    if asset.repoint_file_dependency_name and asset.repoint_file_dependency_name not in repoint_jobs_created:
        repoint_file_jobs.append(repoint_file.build_repoint_file_deps_asset(asset.repoint_file_dependency_name, asset.partitions_def))
        repoint_jobs_created.add(asset.repoint_file_dependency_name)

assets = spice_jobs + batch_jobs + spin_jobs + repoint_file_jobs

# Now for each asset, create a sensor using handler.build_sensor().
# store in sensors list
sensors = sensors


defs = Definitions(
    assets=assets + idex.L0_asset,
    sensors=spin.sensors
    + repoint_file.sensors
    + custom_partitions.sensors
    + spice.sensors
    + sensors
    + idex.L0_sensor,
)