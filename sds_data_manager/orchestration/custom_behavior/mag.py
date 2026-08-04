"""Override behavior for MAG processing."""

import datetime
import re

from imap_data_access import processing_input

from sds_data_manager.orchestration import imap_job, types
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


@JobBuilderRegistry.register("mag", "l1c", "norm-mago")
@JobBuilderRegistry.register("mag", "l1c", "norm-magi")
class MagL1CJob(imap_job.IMAPJobHandler):
    """Deliver the previous day's L1C to MAG L1C processing.

    MAG L1C continues the previous day's L1C timeline across the day boundary
    when the current day opens with a gap (imap_processing#3323). The previous
    day's L1C is this job's own output product, so it is deliberately not
    declared as an input in imap_mag_dependencies.yaml: a declared self-input
    would put a cycle in the Dagster asset graph, and the generic input query
    would feed a reprocessing run its own earlier output. The file is fetched
    here at input-collection time instead. If the previous day's L1C does not
    exist (or its job has not finished), processing proceeds with the current
    day alone. Reprocessing runs are not ordered by date, so a reprocessed
    day can inherit the previous generation's L1C from the day before;
    reprocess in date order when regenerated timeline continuity matters.
    """

    def get_science_files_inputs(self, context, target_start, target_end):
        """Return the base science inputs plus the previous day's L1C, if any."""
        science_processing_inputs = super().get_science_files_inputs(
            context, target_start, target_end
        )

        previous_day_l1c = types.DependencyNode(
            source="mag",
            data_type="l1c",
            descriptor=self.job_config.descriptor,
            required=False,
            trigger_job=False,
        )
        # The query window ends at target_start so that the strict overlap
        # check in get_all_files_in_time_range cannot match the current day's
        # own partition, which starts exactly at target_start. The job never
        # receives its own output as input.
        metadata_list = previous_day_l1c.get_all_files_in_time_range(
            context, target_start - datetime.timedelta(days=1), target_start
        )

        previous_day_files = []
        for metadata in metadata_list:
            if "file_names" in metadata:
                # Dagster wraps metadata in a MetadataValue object
                file_names = metadata["file_names"].value
                # Handle both single strings and lists of files safely
                if isinstance(file_names, str):
                    file_names = [file_names]
                previous_day_files.extend(file_names)

        if not previous_day_files:
            context.log.info(
                "No previous day L1C found; MAG L1C processes this day alone."
            )
            return science_processing_inputs

        # Apply the same version-renaming strategy as
        # IMAPJobHandler.get_science_files_inputs so the previous day's file is
        # named consistently with the base science inputs.
        pattern = re.compile(r"v(\d{3})\.(cdf|pkts)$")
        renamed_previous_day_files = [
            pattern.sub(r"v001.0\1.\2", file) for file in previous_day_files
        ]
        context.log.info(
            f"MAG L1C adding the previous day's L1C: {renamed_previous_day_files}"
        )
        science_processing_inputs.append(
            processing_input.ScienceInput(*list(set(renamed_previous_day_files)))
        )

        return science_processing_inputs
