from dagster import Definitions
from imap_data_access import VALID_DATALEVELS
from sds_data_manager.orchestration import custom_partitions, repoint_file, spice, idex, \
    reprocessing
from sds_data_manager.orchestration import spin
from sds_data_manager.orchestration.imap_file import IMAPAncillaryFileHandler, IMAPScienceFileHandler
from sds_data_manager.orchestration.imap_job import IMAPJobHandler
from sds_data_manager.orchestration.dependency import (
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
spice_assets = []
spin_assets = []
repoint_file_assets = []
unique_job_names = []

# We're going to loop through first to find all outputs
all_outputs = []
for potential_job in all_jobs:
    outputs_list = list(dependency_config.outputs(potential_job))
    for output in outputs_list:
        name = output.to_dagster_asset().to_user_string()
        all_outputs.append(name)
    
for potential_job in all_jobs:
    partition = dependency_config.partition(potential_job)   
    inputs_list = list(dependency_config.inputs(potential_job)) 
    source, data_type, descriptor = potential_job

    if data_type in VALID_DATALEVELS:
        if ('3mo' not in descriptor) and ('6mo' not in descriptor) and ('1yr' not in descriptor): # Skip maps for now
            non_l0_job = IMAPJobHandler(dependency_config._config[potential_job])
            if non_l0_job.job_config.spice_input:
                asset_name = non_l0_job.job_config.spice_input.to_dagster_asset().to_user_string()
                if asset_name not in unique_job_names:
                    unique_job_names.append(asset_name)
                    spice_assets.append(spice.build_spice_deps_asset(non_l0_job.job_config.spice_input, non_l0_job.partitions_def, non_l0_job.job_config.spice_types))
            if non_l0_job.job_config.spin_input:
                asset_name = non_l0_job.job_config.spin_input.to_dagster_asset().to_user_string()
                if asset_name not in unique_job_names:
                    unique_job_names.append(asset_name)
                    spin_assets.append(spin.build_spin_deps_asset(non_l0_job.job_config.spin_input, non_l0_job.partitions_def))
            if non_l0_job.job_config.repoint_input:
                asset_name = non_l0_job.job_config.repoint_input.to_dagster_asset().to_user_string()
                if asset_name not in unique_job_names:
                    unique_job_names.append(asset_name)
                    repoint_file_assets.append(repoint_file.build_repoint_file_deps_asset(non_l0_job.job_config.repoint_input, non_l0_job.partitions_def))
            if non_l0_job.job_config.to_dagster_asset().to_user_string() not in unique_job_names:
                non_l0_job_handlers.append(non_l0_job)
                unique_job_names.append(non_l0_job.job_config.to_dagster_asset().to_user_string())

            # Finally, check for inputs that do not have a corresponding output.
            for input in inputs_list:
                input_name = input.to_dagster_asset().to_user_string()
                if (input_name not in all_outputs) and (input_name not in unique_job_names):
                    if "_ancillary_" in input_name:
                        ancillary_handlers.append(IMAPAncillaryFileHandler(input, non_l0_job.partitions_def))
                        unique_job_names.append(input_name)
                    elif "idex_l0_" in input_name:
                        # Continue because IDEX will defined custom asset and sensor below.
                        continue
                    elif "spice" in input_name:
                        continue
                    elif "spin" in input_name:
                        continue
                    elif "repoint" in input_name:
                        continue
                    else:
                        l0_job_handlers.append(IMAPScienceFileHandler(input, non_l0_job.partitions_def))
                        unique_job_names.append(input_name)

# Now using handlers, create assets for each handler:
#   1. create asset using handler.build_asset()
#   2. create assets for spice, spin, and a repoint file
# store in assets list
assets_to_build = ancillary_handlers + l0_job_handlers + non_l0_job_handlers

sensors = []
batch_jobs = []
for asset in assets_to_build:
    batch_jobs.append(asset.build_asset())
    sensors.append(asset.build_sensor())

assets = spice_assets + batch_jobs + spin_assets + repoint_file_assets

defs = Definitions(
    assets=assets + idex.L0_asset,
    sensors=spin.sensors
    + repoint_file.sensors
    + custom_partitions.sensors
    + spice.sensors
    + sensors
    + reprocessing.sensors
    + idex.L0_sensor,
)