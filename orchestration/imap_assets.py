from orchestration.imap_file import IMAPScienceFileHandler, IMAPAncillaryFileHandler
from orchestration.imap_job import IMAPJobHandler
from orchestration import custom_partitions
from imap_data_access import VALID_DATALEVELS
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import DependencyConfigReader


# This is done once. TODO: what to do if there are changes to the YAML?

dependency_config = DependencyConfigReader()

ancillary_handlers = []
l0_job_handlers = []
non_l0_job_handlers = []

# Each key in _config is a downstream job (source, data_type, descriptor).
# Bucket each job into the right handler list based on its data_type.
for potential_job in dependency_config._config.keys():
    source, data_type, descriptor = potential_job
    asset_name = f"{source}_{data_type}_{descriptor}"
    partition = dependency_config.partition(potential_job)
    if data_type == "ancillary":
        ancillary_handlers.append(IMAPAncillaryFileHandler(asset_name))
    elif source == ["idex", "ultra"]:
        # Continue because IDEX will defined custom asset and sensor below.
        continue
    elif data_type == "l0":
        l0_job_handlers.append(IMAPScienceFileHandler(asset_name, partition))
    elif data_type in VALID_DATALEVELS:
        non_l0_job_handlers.append(IMAPJobHandler(asset_name, partition))

# Now using handlers, create assets for each handler:
#   1. create asset using handler.build_asset()
#   2. create assets for spice, spin, and attitude_pointing
# store in assets list
assets_to_build = ancillary_handlers + l0_job_handlers + non_l0_job_handlers
batch_jobs = [x.build_asset() for x in assets_to_build]
spice_jobs = [x.build_spice_deps_asset() for x in assets_to_build if x.needs_spice]
spin_jobs = [x.build_spin_deps_asset() for x in assets_to_build if x.needs_spin]
attitude_pointing_jobs = [x.build_attitude_pointing_deps_asset() for x in assets_to_build if x.needs_pointing_attitude]

assets=spice_jobs+batch_jobs+spin_jobs+attitude_pointing_jobs

# Now for each asset, create a sensor using handler.build_sensor().
# store in sensors list
sensors = [x.build_sensor() for x in assets_to_build]