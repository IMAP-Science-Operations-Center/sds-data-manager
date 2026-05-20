from orchestration.imap_file import IMAPScienceFileHandler, IMAPAncillaryFileHandler
from orchestration.imap_job import IMAPJobHandler
from orchestration import custom_partitions

from imap_data_access import VALID_DATALEVELS
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import DependencyConfigReader


# This is done once. TODO: what to do if there are changes to the YAML?

dependency_config = DependencyConfigReader()

for potential_job in dependency_config._config.keys():
    if potential_job[0] == "glows":
        print(potential_job)
        for dep in dependency_config.inputs(potential_job):
            print(f"  {dep}")
        for dep in dependency_config.outputs(potential_job):
            print(f"  outputs: {dep}")
# for data_type 'ancillary', create IMAPAncillaryFileHandler
# for data_type in VALID_DATALEVELS == 'l0', create IMAPScienceFileHandler. pass in partition
# for data_type in VALID_DATALEVELS other than 'l0', create IMAPJobHandler. pass in partition


# Now using handlers, create assets for each handler:
#   1. create asset using handler.build_asset()
#   2. create assets for spice, spin, and attitude_pointing
# store in assets list

# Now for each asset, create a sensor using handler.build_sensor().
# store in sensors list