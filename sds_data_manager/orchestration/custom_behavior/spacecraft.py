"""Override behavior for Spacecraft processing."""

import datetime
import json
import os

import imap_data_access
from dagster import (
    RunRequest,
    SensorEvaluationContext,
    sensor,
)
from imap_data_access.file_validation import Version

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import (
    imap_job,
)
from sds_data_manager.orchestration.dagster_utilities import (
    get_materialization_result,
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


@JobBuilderRegistry.register("spacecraft", "l1a", "pointing-attitude")
class SpacecraftPointingAttitudeJob(imap_job.IMAPJobHandler):
    """Overriding parts of the spacecraft processing pipeline."""

    def get_science_files_inputs(self, context, target_start, target_end):
        """Override default behavior to return nothing."""
        return []

    def _determine_output_versions(
        self,
        session: db.Session,
        start_date: datetime.datetime,
        repointing: int | None = None,
        dependency_inputs=None,
    ) -> dict[str, dict[str, int]]:
        """Version this job's output to match its attitude_history kernel input.

        The processing code stamps the output pointing-attitude kernel with
        the version of the attitude_history kernel it was built from -- the
        last (most current) attitude_history kernel among the SPICE files
        actually resolved for this run -- not an independently-incremented
        processing-attempt counter like the base class's
        ProcessingJob-table-based scheme.

        We read that kernel's version directly off `dependency_inputs` (the
        exact file list get_dependencies() already resolved and that becomes
        the dependency JSON) rather than re-querying models.SPICEFiles
        ourselves. A separate query would need to replicate the metakernel
        API's own selection rules (it picks by ingestion-timestamp priority
        over the full [start, end] window, not by version number over a
        single point), and superseded attitude_history rows are never
        deleted from the DB -- so an independent query could easily land on
        a different kernel than the one actually used for this run.
        """
        resolved_inputs = (
            dependency_inputs.processing_input if dependency_inputs else []
        )
        spice_input = next(
            (inp for inp in resolved_inputs if inp.data_type == "spice"), None
        )
        ah_files = [
            filename
            for filename in (spice_input.filename_list if spice_input else [])
            if imap_data_access.SPICEFilePath(filename).spice_metadata["type"]
            == "attitude_history"
        ]
        if not ah_files:
            raise ValueError(
                "No attitude_history kernel found in this run's resolved SPICE "
                "dependencies -- cannot determine minor_version."
            )
        version = int(
            imap_data_access.SPICEFilePath(ah_files[-1]).spice_metadata["version"]
        )

        output = self.job_config.outputs[0]
        return {
            output.descriptor: {
                "minor_version": version,
                "major_version": output.major_version,
            }
        }

    def find_outputs(
        self,
        context,
        session: db.Session,
        output_versions: dict | None = None,
        start_date: datetime.datetime | None = None,
        repointing: int | None = None,
        inputs: dict | None = None,
    ):
        """Find the SPICE kernel produced by this job, if any.

        Unlike science products, this job's output is a SPICE kernel indexed
        into models.SPICEFiles (kernel_type "pointing_attitude") rather than
        models.ScienceFiles: that table has no
        instrument/data_level/descriptor/repointing columns, and uses a single
        incrementing `version` int rather than major/minor.

        try_to_submit_job() only ever tells the batch job a *date*, not a
        time (start_date is formatted with "%Y%m%d"), so the produced
        kernel's coverage can only be expected to start/end on the same day
        as the partition's start/end -- not at the exact same instant.
        When we know what minor version this run was submitted with, we
        also require the kernel's version to match it, since the
        processing code stamps the output kernel with that same version
        number: that's what actually distinguishes this run's kernel from
        one produced by a different (e.g. later) run covering a similar or
        overlapping window.
        """
        output = self.job_config.outputs[0]
        _, end_date = parse_dates_from_partition_key(context.partition_key)

        filters = [models.SPICEFiles.kernel_type == "pointing_attitude"]
        if start_date is not None:
            day_start = datetime.datetime.combine(
                start_date.date(), datetime.time.min, tzinfo=datetime.timezone.utc
            )
            filters.append(models.SPICEFiles.min_date_datetime >= day_start)
            filters.append(
                models.SPICEFiles.min_date_datetime
                < day_start + datetime.timedelta(days=1)
            )
        if end_date is not None:
            day_start = datetime.datetime.combine(
                end_date.date(), datetime.time.min, tzinfo=datetime.timezone.utc
            )
            filters.append(models.SPICEFiles.max_date_datetime >= day_start)
            filters.append(
                models.SPICEFiles.max_date_datetime
                < day_start + datetime.timedelta(days=1)
            )
        if output_versions is not None and output.descriptor in output_versions:
            filters.append(
                models.SPICEFiles.version
                == output_versions[output.descriptor]["minor_version"]
            )

        created_kernel = (
            session.query(models.SPICEFiles)
            .filter(*filters)
            .order_by(
                models.SPICEFiles.version.desc(),
                models.SPICEFiles.ingestion_date.desc(),
            )
            .first()
        )
        if not created_kernel:
            expected_version = (
                output_versions[output.descriptor]["minor_version"]
                if output_versions is not None and output.descriptor in output_versions
                else None
            )
            context.log.info(
                f"No pointing_attitude SPICEFiles row found for partition "
                f"{context.partition_key!r} (start_date={start_date}, "
                f"end_date={end_date}, expected_version={expected_version}). "
                "No materialization will be emitted for this run."
            )
            return []

        context.log.info(
            f"""Found file {os.path.basename(created_kernel.file_path)}!
                Creating Asset.
            """
        )
        materialization = get_materialization_result(
            context,
            output.to_dagster_asset(),
            context.partition_key,
            [os.path.basename(created_kernel.file_path)],
            Version(created_kernel.version, 0),
            "spice",
            inputs=inputs,
        )
        return [materialization] if materialization else []

    def build_sensor(self):
        """Return a Dagster sensor monitoring for new dependencies.

        By running on each new repoint partition, rather than new repoint files,
        we ensure that dagster has been given enough time to actually create the
        new custom partitions.

        """
        sensor_name = f"{self.job_config.to_dagster_name()}_kickoff_sensor"

        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=self.sensor_run_frequency,
        )
        def _sensor(context: SensorEvaluationContext):

            # Create a unique suffix for this sensor trigger
            job_suffix = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor_str = context.cursor or "[]"
            processed_partitions = set(json.loads(cursor_str))

            # Query the instance for the current state of the dynamic partitions
            current_partitions = set(
                context.instance.get_dynamic_partitions(self.partitions_def.name)
            )
            # Determine the delta
            new_partitions = current_partitions - processed_partitions

            # Yield a RunRequest for each new partition
            for partition_key in new_partitions:
                run_key = "_".join(
                    [
                        self.job_config.to_dagster_name(),
                        partition_key,
                        job_suffix,
                    ]
                )
                yield RunRequest(
                    run_key=run_key,
                    partition_key=partition_key,
                )

            # Update the cursor so we don't process these again
            # We store the full current set so the next tick has the latest baseline
            context.update_cursor(json.dumps(list(current_partitions)))

        return _sensor
