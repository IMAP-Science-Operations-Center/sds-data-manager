from orchestration.imap_file import IMAPScienceFileHandler, IMAPAncillaryFileHandler
from orchestration.imap_job import IMAPJobHandler
from orchestration import custom_partitions

ancillary_files = [
    IMAPAncillaryFileHandler("glows_ancillary_pipeline-settings"),
    IMAPAncillaryFileHandler("glows_ancillary_l1b-conversion-table-for-anc-data"),
    IMAPAncillaryFileHandler("glows_ancillary_l1b-exclusions-by-instr-team"),
    IMAPAncillaryFileHandler("glows_ancillary_l1b-map-of-excluded-regions"),
    IMAPAncillaryFileHandler("glows_ancillary_l1b-map-of-uv-sources"),
    IMAPAncillaryFileHandler("glows_ancillary_l1b-suspected-transients"),
    IMAPAncillaryFileHandler("glows_ancillary_l2-calibration"),
    IMAPAncillaryFileHandler("glows_ancillary_time-dep-bckgrd"),
    IMAPAncillaryFileHandler("glows_ancillary_map-of-extra-helio-bckgrd"),
    IMAPAncillaryFileHandler("glows_ancillary_l3a-map-of-extra-helio-bckgrd"),
    IMAPAncillaryFileHandler("glows_ancillary_l3a-time-dep-bckgrd"),
    IMAPAncillaryFileHandler("glows_ancillary_calibration-data"),
]

l0_files = [IMAPScienceFileHandler("glows_l0_raw", custom_partitions.repoint_partitions)]

jobs = [IMAPJobHandler("glows_l1a_all", custom_partitions.repoint_partitions),
        IMAPJobHandler("glows_l1b_de", custom_partitions.repoint_partitions),
        IMAPJobHandler("glows_l1b_hist", custom_partitions.repoint_partitions),
        IMAPJobHandler("glows_l2_hist", custom_partitions.repoint_partitions),
        IMAPJobHandler("glows_l3a_hist", custom_partitions.repoint_partitions),]

assets_to_build = ancillary_files + l0_files + jobs


batch_jobs = [x.build_asset() for x in assets_to_build]
spice_jobs = [x.build_spice_deps_asset() for x in assets_to_build if x.needs_spice]
spin_jobs = [x.build_spin_deps_asset() for x in assets_to_build if x.needs_spin]
repoint_file_jobs = [x.build_repoint_file_deps_asset() for x in assets_to_build if x.needs_repoint_file]

assets=spice_jobs+batch_jobs+spin_jobs+repoint_file_jobs
sensors=[x.build_sensor() for x in assets_to_build]