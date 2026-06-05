from dagster import Definitions
from imap_data_access import VALID_DATALEVELS
from sds_data_manager.orchestration import custom_partitions, idex, hi, reprocessing
from sds_data_manager.orchestration.imap_file import  IMAPScienceFileHandler
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
        name = output.to_dagster_asset().to_user_string()
        all_outputs.append(name)

# Next, we'll gather up all the job and file handlers
for potential_job in all_jobs:
    partition = dependency_config.partition(potential_job)   
    inputs_list = list(dependency_config.inputs(potential_job)) 
    source, data_type, descriptor = potential_job

    if data_type in VALID_DATALEVELS:
        if ('3mo' not in descriptor) and ('6mo' not in descriptor) and ('1yr' not in descriptor): # Skip maps for now
            if 'goodtimes' in descriptor and source == 'hi' and data_type=='l1b':
                job = hi.HiGoodtimesJob(dependency_config._config[potential_job])
            else:
                job = IMAPJobHandler(dependency_config._config[potential_job])
                
            if job.job_config.to_dagster_asset().to_user_string() not in unique_job_names:
                job_handlers.append(job)
                unique_job_names.append(job.job_config.to_dagster_asset().to_user_string())

            # Finally, check for inputs that do not have a corresponding output.
            for input in inputs_list:
                input_name = input.to_dagster_asset().to_user_string()
                if (input_name not in all_outputs) and (input_name not in unique_job_names):
                    if "_ancillary_" in input_name:
                        continue
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
                        file_handlers.append(IMAPScienceFileHandler(input, job.partitions_def))
                        unique_job_names.append(input_name)

# Now using handlers, create assets for each handler:
#   1. create asset using handler.build_asset()
#   2. create assets for spice, spin, and a repoint file
# store in assets list
assets_to_build = job_handlers + file_handlers

sensors = []
batch_jobs = []
for asset in assets_to_build:
    batch_jobs.append(asset.build_asset())
    sensors.append(asset.build_sensor())

assets = batch_jobs

defs = Definitions(assets = assets 
                   + idex.L0_asset,
                   sensors = custom_partitions.sensors
                   + sensors
                   + reprocessing.sensors
                   + idex.L0_sensor,
)