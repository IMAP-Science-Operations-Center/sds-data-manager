"""Override behavior for Spacecraft processing."""

from sds_data_manager.orchestration import imap_job
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


@JobBuilderRegistry.register("spacecraft", "l1a", "pointing-attitude")
class SpacecraftPointingAttitudeJob(imap_job.IMAPJobHandler):
    """Overriding parts of the spacecraft processing pipeline."""

    def get_science_files_inputs(self, context, target_start, target_end):
        """Override default behavior to return nothing."""
        return []
