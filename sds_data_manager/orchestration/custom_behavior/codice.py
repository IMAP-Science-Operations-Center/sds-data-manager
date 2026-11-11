"""Include both SCI-LUTs when a CoDICE daily partition spans a table update."""

import datetime
import os

from imap_data_access import processing_input

from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.imap_job import IMAPJobHandler
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


@JobBuilderRegistry.register("codice", "l1a", "all")
class CodiceL1aJob(IMAPJobHandler):
    """Keep the previous table available on the new SCI-LUT's effective day."""

    def get_ancillary_files_inputs(self, session, target_start, target_end):
        """Add the preceding SCI-LUT on a transition day, preserving other inputs.

        Ancillary validity has day precision, while the onboard table can change
        during that day. The preceding LUT can therefore end the day before the
        selected LUT starts and still be needed by that day's telemetry. Select
        its latest version independently of the selected LUT's version.
        """
        inputs = super().get_ancillary_files_inputs(session, target_start, target_end)
        table = models.AncillaryFiles
        for index, item in enumerate(inputs):
            if item.source != "codice" or item.descriptor != "l1a-sci-lut":
                continue
            selected_start = datetime.datetime.strptime(
                item.imap_file_paths[0].start_date, "%Y%m%d"
            )
            if not target_start <= selected_start < target_end:
                continue
            previous = (
                session.query(table)
                .filter(
                    table.instrument == "codice",
                    table.descriptor == "l1a-sci-lut",
                    table.start_date < selected_start,
                )
                .order_by(table.start_date.desc(), table.version.desc())
                .first()
            )
            if previous is not None:
                inputs[index] = processing_input.AncillaryInput(
                    *item.filename_list, os.path.basename(previous.file_path)
                )
        return inputs
