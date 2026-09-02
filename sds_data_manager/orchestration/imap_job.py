"""IMAP job handler for managing dependencies and job submission."""

import datetime
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from dagster import (
    AssetExecutionContext,
    AssetObservation,
    AssetOut,
    DagsterEventType,
    DagsterRunStatus,
    EventRecordsFilter,
    Failure,
    RetryRequested,
    RunRequest,
    RunsFilter,
    SensorEvaluationContext,
    define_asset_job,
    multi_asset,
    sensor,
)
from imap_data_access import VALID_DATALEVELS, DependencyFilePath, processing_input
from imap_data_access.file_validation import Version
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from sds_data_manager.lambda_code.SDSCode.api_lambdas import upload_api
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import (
    config,
    custom_partitions,
    dagster_utilities,
    repoint_file,
    spice,
    spin,
)
from sds_data_manager.orchestration.dagster_utilities import get_materialization_result
from sds_data_manager.orchestration.dependency import DependencyConfigReader
from sds_data_manager.orchestration.types import DependencyNode, ProcessingJobNode

BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")
# Create an ECR client for getting container image digests
ECR_CLIENT = boto3.client("ecr", region_name="us-west-2")
# Define the retry strategy for batch jobs
BATCH_JOB_RETRY_STRATEGY = {
    "attempts": 10,
    "evaluateOnExit": [
        {
            "onExitCode": "75",
            "action": "RETRY",
        },  # retry jobs that failed with the rerunnable exit code.
        {"onStatusReason": "CannotPullContainerError*", "action": "RETRY"},
        {"onReason": "*", "action": "EXIT"},
    ],
}
# Create an sqs client
SQS_CLIENT = boto3.client("sqs", region_name="us-west-2")
# Create a client to access Cloudwatch
LOGS_CLIENT = boto3.client("logs", region_name="us-west-2")
LOG_GROUP_NAME = "/aws/batch/job"

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

priority_levels = {
    "l0": "0",
    "l1": -1,
    "l1a": "-2",
    "l1b": "-3",
    "l1c": "-4",
    "l1d": "-5",
    "l2": "-6",
    "l2a": "-7",
    "l2b": "-8",
    "l2c": "-9",
    "l2d": "-10",
    "l3": "-11",
    "l3a": "-12",
    "l3b": "-13",
    "l3c": "-14",
    "l3d": "-15",
}

partition_map = {
    "daily": custom_partitions.daily_partitions,
    "repoint": custom_partitions.repoint_partitions,
    "10d": custom_partitions.idex10_partitions,
    # NOTE: Right now, IDEX is the only instrument who uses 1mo cadence job that
    # maps to exactly 30 days. If this changes, this logic will need update.
    "30d": custom_partitions.idex30_partitions,
} | custom_partitions.CADENCE_PARTITION_DEFS


class MissingDependenciesError(Exception):
    """Error to raise from get_dependencies function if crucial data is not found."""

    pass


@dataclass
class BatchJobSubmit:
    """Class to store information about a batch job submission."""

    status: str
    message: str
    # the ProcessingJob information as a dictionary.
    job: dict | None = None
    response: dict | None = None


class IMAPJobHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, job: ProcessingJobNode):
        """Initialize handler with job node and process dependencies.

        Parameters
        ----------
        job : ProcessingJobNode
            The job node to process.
        """
        self.BATCH_JOB_TIMEOUT_SECONDS = 18000  # 5 hours
        self.WAIT_TIME_AFTER_BATCH_SECONDS = (
            60  # time to wait after a batch job completes to search for files.
        )
        self.job_config = job

        self.partitions_def = partition_map.get(self.job_config.partition)
        self.sensor_run_frequency = config.sensor_schedules.get(
            self.job_config.data_type, 600
        )
        self.dependency_config_reader = DependencyConfigReader()
        outputs_for_job = [x.to_dagster_asset() for x in self.job_config.outputs]
        self.dagster_job_name = f"{self.job_config.to_dagster_name()}_processing_job"
        self.dagster_job = define_asset_job(
            name=self.dagster_job_name,
            selection=outputs_for_job,
            tags={
                "dagster/priority": priority_levels.get(self.job_config.data_type, "0")
            },
        )
        self.triggering_input_names = [
            dep.to_dagster_name() for dep in self.job_config.triggering_deps
        ]

    def build_asset(self):
        """Create an Asset in Dagster for a particular data product."""
        input_keys = [dep.to_dagster_asset() for dep in self.job_config.inputs]
        output_assets = {}
        for out in self.job_config.outputs:
            output_assets[out.to_dagster_name()] = AssetOut(is_required=False)

        @multi_asset(
            name=f"{self.job_config.to_dagster_name()}_multi_asset_op",
            deps=input_keys,
            partitions_def=self.partitions_def,
            outs=output_assets,
        )
        def _generic_batch_submitter(context: AssetExecutionContext):
            yield from self.run_job(
                context,
                self.BATCH_JOB_TIMEOUT_SECONDS,
                self.WAIT_TIME_AFTER_BATCH_SECONDS,
            )

        # Return the generated function back to Dagster
        return _generic_batch_submitter

    def run_job(
        self,
        context: AssetExecutionContext,
        batch_job_timeout: int,
        time_to_wait_after_batch_query: int,
    ):
        """Perform all steps needed to run the Batch job.

        This function will:

        1) Get all dependencies from the dependency tree
        2) Check if the job had been submitted before
           a) If it has, and Dagster doesn't know about it, then it will materialize
              the asset
           b) If Dagster does know about it, we exit
        3) Get the Job version
        4) Submit the job
        5) Wait for the output files,
           and materialize them as we see them in the database.

        """
        # Before doing anything, check if any of the dependencies are currently
        # running or about to run.
        # If so, let us try again in 5 minutes.
        dependencies_running = self._check_for_running_dependencies(context)
        if dependencies_running:
            context.log.info("Retrying job in 5 minutes.")
            raise RetryRequested(max_retries=10, seconds_to_wait=300)
        # Figure out what time window this specific run is responsible for
        target_partition = context.partition_key
        target_start, target_end = dagster_utilities.parse_dates_from_partition_key(
            target_partition
        )

        # Get the repoint number of the job, based on the partition name.
        parts = context.partition_key.split("_")
        target_pointing_number = None
        if "repoint" in parts[0]:
            target_pointing_number = int(parts[0][7:])

        # Get Dependencies
        with db.Session() as session:
            try:
                dependency_inputs = self.get_dependencies(
                    session, context, target_start, target_end
                )
            except MissingDependenciesError as e:
                context.log.info(f"Skipping job: {e}")
                for output in self.job_config.outputs:
                    yield AssetObservation(
                        asset_key=output.to_dagster_asset(),
                        partition=context.partition_key,
                        metadata={
                            "status": "Skipped - Missing Dependencies",
                            "missing_files": str(e),
                        },
                    )
                return

            context.log.info(
                f"Using the following dependencies: {dependency_inputs.serialize()}"
            )
            # We have the dependencies, lets try to submit the job!
            output_versions = self._determine_output_versions(
                session=session,
                start_date=target_start,
                repointing=target_pointing_number,
            )
            context.log.info(f"Job Versions to Use: {output_versions}")

            submit_response = self.try_to_submit_job(
                session,
                target_start,
                output_versions,
                dependency_inputs.serialize(),
                repoint=target_pointing_number,
            )
            context.log.info(
                f"""Submit response: {submit_response.status}
                    - {submit_response.message},
                    {submit_response.job}"""
            )

            if submit_response.status == "submitted":
                batch_status = self.wait_for_batch_job(
                    context,
                    session,
                    submit_response.response,
                    batch_job_timeout,
                    time_to_wait_after_batch_query,
                )
                time.sleep(
                    time_to_wait_after_batch_query
                )  # Give the indexer time to pick up the files

                output_files = self.find_outputs(
                    context,
                    session,
                    output_versions=output_versions,
                    start_date=target_start,
                    repointing=target_pointing_number,
                    inputs=dependency_inputs.serialize(),
                )
                for f in output_files:
                    yield f
                if batch_status == models.Status.FAILED:
                    raise Failure(
                        description="Batch Job Failure. View logs for details."
                    )
            elif submit_response.status == "skipped":
                # If we skipped the job, let us materialize any previous outputs
                # so that we ensure dagster knows about them

                output_files = self.find_outputs(
                    context,
                    session,
                    start_date=target_start,
                    repointing=target_pointing_number,
                    inputs=dependency_inputs.serialize(),
                )
                for f in output_files:
                    yield f
            else:
                raise Failure(description=submit_response.message)

    def wait_for_batch_job(
        self,
        context,
        session: db.Session,
        job_response: dict,
        timeout: int,
        wait_time: int,
    ):
        """Wait for a Batch job to complete, and return the status."""
        final_status = models.Status.FAILED
        timeout_start = time.time()
        while time.time() < timeout_start + timeout:
            # We injected our table ID into the job name
            job_database_id = job_response["jobName"].split("-")[-1]
            describe_response = BATCH_CLIENT.describe_jobs(jobs=[job_response["jobId"]])
            if not describe_response.get("jobs"):
                context.log.info("Job not found. It may have been deleted.")
                break
            job_info = describe_response["jobs"][0]
            if job_info["status"] == "SUCCEEDED":
                final_status = models.Status.SUCCEEDED
                break
            elif job_info["status"] == "FAILED":
                break
            else:
                time.sleep(wait_time)
                continue

        # Print out some logs
        container_info = job_info.get("container", {})
        log_stream_name = container_info.get("logStreamName")
        if log_stream_name:
            context.log.info(
                f"\nFetching the last 100 lines from log stream: {log_stream_name}"
            )
            try:
                # Fetch the latest 100 log events
                log_response = LOGS_CLIENT.get_log_events(
                    logGroupName=LOG_GROUP_NAME,
                    logStreamName=log_stream_name,
                    limit=100,
                    startFromHead=False,
                )

                events = log_response.get("events", [])

                if not events:
                    context.log.info("No logs found in the stream.")
                else:
                    context.log.info("-" * 40)
                    for event in events:
                        context.log.info(event["message"])
                    context.log.info("-" * 40)

            except Exception as e:
                context.log.info(f"Failed to fetch logs: {e}")
        else:
            context.log.info(
                "No logStreamName found. "
                "The job may have failed before the container could start."
            )

        # Gather information about start/stop timing
        started_at_timestamp = job_info.get("startedAt", job_info.get("createdAt"))
        stopped_at_timestamp = job_info.get("stoppedAt")
        started_at = (
            datetime.datetime.fromtimestamp(
                started_at_timestamp / 1000, tz=datetime.timezone.utc
            )
            if started_at_timestamp is not None
            else None
        )
        stopped_at = (
            datetime.datetime.fromtimestamp(
                stopped_at_timestamp / 1000, tz=datetime.timezone.utc
            )
            if stopped_at_timestamp is not None
            else None
        )

        # Fetch the row in the database
        job = session.get(models.ProcessingJob, job_database_id)

        # Make updates to the database table
        job.status = final_status
        job.job_definition = job_info["jobDefinition"]
        job.job_log_stream_id = log_stream_name
        job.container_image = container_info.get("image", "")
        job.started_at = started_at
        job.stopped_at = stopped_at
        session.commit()

        # Return either Success or Failure
        return final_status

    def find_outputs(
        self,
        context,
        session: db.Session,
        output_versions: dict | None = None,
        start_date: datetime.datetime | None = None,
        repointing: int | None = None,
        inputs: dict | None = None,
    ):
        """Return all output from a job given a particular start_date/repointing."""
        output_materializations = []

        if start_date is None and repointing is None:
            raise ValueError(
                "You must at least provide either start_date or repointing"
            )

        for output in self.job_config.outputs:
            filters = [
                models.ScienceFiles.instrument == output.source,
                models.ScienceFiles.data_level == output.data_type,
                models.ScienceFiles.descriptor == output.descriptor,
            ]
            if output_versions is not None:
                if output.descriptor in output_versions.keys():
                    versions = output_versions[output.descriptor]
                    filters.append(
                        models.ScienceFiles.major_version == versions["major_version"]
                    )
                    filters.append(
                        models.ScienceFiles.minor_version == versions["minor_version"]
                    )
            if repointing is not None:
                filters.append(models.ScienceFiles.repointing == int(repointing))
            if start_date is not None:
                filters.append(models.ScienceFiles.start_date == start_date.date())
            created_file = (
                session.query(models.ScienceFiles)
                .filter(*filters)
                .distinct(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                )
                .order_by(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                    models.ScienceFiles.major_version.desc(),
                    models.ScienceFiles.minor_version.desc(),
                )
                .first()
            )
            if created_file:
                context.log.info(
                    f"""Found file {os.path.basename(created_file.file_path)}!
                        Creating Asset.
                    """
                )
                materialization = get_materialization_result(
                    context,
                    output.to_dagster_asset(),
                    context.partition_key,
                    [os.path.basename(created_file.file_path)],
                    Version(created_file.major_version, created_file.minor_version),
                    "science",
                    inputs=inputs,
                )
                if materialization:
                    output_materializations.append(materialization)
        return output_materializations

    def _check_for_running_dependencies(self, context):
        """Check if anything upstream of this file is currently running."""
        # Imported locally to avoid a circular import
        from sds_data_manager.orchestration.imap_dagster import defs  # noqa: PLC0415

        # Get all ancestral upstream assets.
        input_set = set(
            defs.get_repository_def().asset_graph.get_ancestor_asset_keys(
                self.job_config.outputs[0].to_dagster_asset()
            )
        )
        in_flight_runs = context.instance.get_runs(
            filters=RunsFilter(
                statuses=[
                    DagsterRunStatus.QUEUED,
                    DagsterRunStatus.STARTING,
                    DagsterRunStatus.STARTED,
                ]
            )
        )
        conflict_found = False
        for run in in_flight_runs:
            if run.asset_selection:
                overlap = input_set.intersection(run.asset_selection)
                if overlap:
                    conflict_found = True
                    context.log.info(
                        f"Upstream job in progress {run.run_id}: {overlap}"
                    )
                    break

        return conflict_found

    def _get_overlapping_target_partitions(
        self, upstream_partition_key, up_start, up_end, instance
    ):
        """Evaluate the upstream date range against existing downstream partitions.

        Returns a list of downstream partition keys that overlap.
        """
        target_keys = []
        # Fetch the currently known dynamic partitions for your target asset
        existing_downstream_keys = instance.get_dynamic_partitions(
            self.partitions_def.name
        )

        # Check if the upstream and downstream are in the same partition
        if upstream_partition_key in existing_downstream_keys:
            # These are actually in same partition!
            return [upstream_partition_key]

        for down_key in existing_downstream_keys:
            down_start, down_end = dagster_utilities.parse_dates_from_partition_key(
                down_key
            )
            if not down_start or not down_end:
                continue

            # Math logic for overlapping date intervals
            if up_start <= down_end and up_end >= down_start:
                target_keys.append(down_key)

        return target_keys

    def build_sensor(self):
        """Return a Dagster sensor monitoring for new dependencies.

        Note that this does not perform all dependency checks.
        That job is part of the @asset's job.
        This job simply alerts the asset if there is the *potential* to start.

        1) Checks for all of the latest asset materializations in dagster
           for all dependencies of this product
        2) Loops through the latest materializations
        3) Determines what time range those materializations belong to
        4) Determines what partition keys of this asset those new materializations
           belong to
        5) For each affected partition, we determine if we really should trigger or not
        6) Yield a RunRequest for a job to make the asset for the
           partitions that have all dependencies.
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

            # Load the cursor to track which events we have already seen.
            # The cursor maps `{science_asset_name: last_processed_storage_id}`.
            # Or `{other_asset_name: last_ingestested_id}`
            cursors = json.loads(context.cursor) if context.cursor else {}
            new_cursors = cursors.copy()
            sensor_start_time = time.time()

            # Iterate through each dependency to find the materializations
            # after the most recent run
            for dependency in self.job_config.inputs:
                target_partitions = []  # Partitions to kick off
                dep_name = dependency.to_dagster_name()
                context.log.info(f"Checking new dependencies for: {dep_name}")
                if dependency.data_type in VALID_DATALEVELS:
                    # New science inputs
                    target_partitions = self.trigger_from_new_science_inputs(
                        context, dependency, new_cursors, sensor_start_time
                    )
                if dependency.data_type == "spice":
                    # New SPICE inputs
                    target_partitions = self.trigger_from_new_non_science_inputs(
                        context,
                        dependency,
                        new_cursors,
                        models.SPICEFiles,
                        models.SPICEFiles.kernel_type,
                        None,
                        "min_date_datetime",
                        "max_date_datetime",
                    )
                elif dependency.data_type == "spin":
                    # New spin inputs
                    target_partitions = self.trigger_from_new_non_science_inputs(
                        context, dependency, new_cursors, models.SpinFiles
                    )
                elif dependency.data_type == "ancillary":
                    # New ancillary inputs
                    target_partitions = self.trigger_from_new_non_science_inputs(
                        context,
                        dependency,
                        new_cursors,
                        models.AncillaryFiles,
                        models.AncillaryFiles.instrument,
                        models.AncillaryFiles.descriptor,
                    )
                # Now we loop through each partition that we received new data for, and
                # determine if we need to start it again.
                for target_partition in target_partitions:
                    # Check if this partition has already been run successfully
                    runs = context.instance.get_runs(
                        filters=RunsFilter(
                            job_name=self.dagster_job_name,
                            statuses=[DagsterRunStatus.SUCCESS],
                            tags={"dagster/partition": target_partition},
                        ),
                        limit=1,  # Limit to 1 since we only care about existence
                    )

                    # If this has never been run,
                    # or we always trigger from this dependency
                    if (dep_name in self.triggering_input_names) or not runs:
                        run_key = "_".join(
                            [
                                self.job_config.to_dagster_name(),
                                target_partition,
                                job_suffix,
                            ]
                        )
                        context.log.info(
                            f"""Yielding a run request with ID:
                               {run_key} on partition {target_partition}.
                               """
                        )

                        # Go to _generic_batch_sumbitter
                        yield RunRequest(
                            partition_key=target_partition, run_key=run_key
                        )

                    elif runs and (dep_name not in self.triggering_input_names):
                        context.log.info(
                            """"We have already materialized something like this,
                            and this dependency does not trigger new processing."""
                        )

                if (time.time() - sensor_start_time) > 30:
                    context.log.info(
                        "Sensor took too long, will inspect new items on the next run. "
                    )
                    break

            # Lock in the new cursor state
            context.update_cursor(json.dumps(new_cursors))

        return _sensor

    def trigger_from_new_non_science_inputs(
        self,
        context,
        dependency,
        cursors,
        table,
        source_column=None,
        descriptor_column=None,
        datetime_start_column="start_date",
        datetime_end_column="end_date",
    ):
        """Return partitions affected by new files arriving in non-science tables."""
        if dependency.source in [
            "leapseconds",
            "spacecraft_clock",
            "imap_frames",
            "science_frames",
            "planetary_constants",
            "planetary_ephemeris",
        ]:
            # Never trigger from these
            return []

        partitions_to_run = []
        dep_name = dependency.to_dagster_name()
        cursor_str = cursors.get(dep_name, config.MISSION_START_TIME)
        latest_ingestion_date = datetime.datetime.fromisoformat(cursor_str).replace(
            tzinfo=datetime.timezone.utc
        )

        # Get filters
        filters = [table.ingestion_date > latest_ingestion_date]
        if source_column:
            filters.append(source_column == dependency.source)
        if descriptor_column:
            filters.append(descriptor_column == dependency.descriptor)

        with db.Session() as session:
            # Get new non-science files
            new_files = session.query(table).filter(*filters).all()

            if new_files:
                latest_ingestion_date = max(f.ingestion_date for f in new_files)

            if dependency.source in spice.NON_TRIGGERING_KERNEL_TYPES:
                context.log.info(
                    f"Skipping trigger evaluation for {dependency.source}."
                )
            elif dependency.source in spice.GROWING_KERNEL_TYPES:
                # Attitude history and pointing attitude kernels grow over time
                # (new segments are appended to the same file series). Using
                # each new file's full min/max coverage here would re-trigger
                # reprocessing of the entire kernel span on every delivery.
                # Instead, narrow the range down to only the coverage that is
                # actually new. See spice.get_growing_kernel_trigger_ranges for
                # the full rules.
                trigger_ranges = spice.get_growing_kernel_trigger_ranges(
                    session, dependency.source, new_files
                )
                partition_set = set()
                for min_dt, max_dt in trigger_ranges:
                    partition_set.update(
                        dagster_utilities.get_affected_partitions(
                            context, self.partitions_def, min_dt, max_dt
                        )
                    )
                partitions_to_run.extend(list(partition_set))
            else:
                for file in new_files:
                    min_dt = getattr(file, datetime_start_column)
                    max_dt = getattr(file, datetime_end_column)
                    if not min_dt or not max_dt:
                        continue
                    partitions = dagster_utilities.get_affected_partitions(
                        context, self.partitions_def, min_dt, max_dt
                    )

                    partitions_to_run.extend(partitions)
            cursors[dep_name] = latest_ingestion_date.isoformat()

        return list(set(partitions_to_run))

    def trigger_from_new_science_inputs(
        self,
        context: AssetExecutionContext,
        dependency: DependencyNode,
        cursors: dict,
        sensor_start_time: float,
    ):
        """Return a list of partitions to trigger from new science files detected."""
        dep_name = dependency.to_dagster_name()
        context.log.info(f"Checking new dependencies for: {dep_name}")

        # Fetch the 10 new science materializations from dagster
        last_event_id = cursors.get(dep_name, 0)
        filter = EventRecordsFilter(
            event_type=DagsterEventType.ASSET_MATERIALIZATION,
            asset_key=dependency.to_dagster_asset(),
            after_cursor=last_event_id,
        )
        new_events = context.instance.get_event_records(
            filter, limit=100, ascending=True
        )

        partitions_to_run = []
        for record in new_events:
            # Update our new cursor marker to the highest storage ID seen
            last_event_id = max(last_event_id, record.storage_id)

            # Get the dates of this new science file
            upstream_partition_key = record.event_log_entry.dagster_event.partition
            up_start, up_end = dagster_utilities.parse_dates_from_partition_key(
                upstream_partition_key
            )
            context.log.info(
                f"""Found one new dependency at
                 {record.event_log_entry.dagster_event.partition}!"""
            )
            if not up_start or not up_end:
                continue

            # Kick off jobs in a range around the file
            # TODO: This is probably too broad. But for now, this
            # is probably fine unless we start getting timeouts.
            if dependency.dependency_query_time_range:
                time_range = int(dependency.dependency_query_time_range[0][0])
                up_start = up_start + datetime.timedelta(days=-time_range)
                up_end = up_end + datetime.timedelta(days=time_range)

            # Calculate overlap between the dependency file and this file
            target_partitions = self._get_overlapping_target_partitions(
                upstream_partition_key, up_start, up_end, context.instance
            )
            partitions_to_run.extend(target_partitions)

            # To keep the sensor short, we'll force it to stop after 30 seconds.
            if (time.time() - sensor_start_time) > 30:
                context.log.info(
                    "Sensor took too long, will inspect new items on the next run. "
                )
                break

        cursors[dep_name] = last_event_id

        return partitions_to_run

    def get_ancillary_files_inputs(
        self, session, target_start: datetime.datetime, target_end: datetime.datetime
    ) -> list[str]:
        """Get the ancillary file dependencies needed to cover a time range."""
        # Query for ancillary files where the start_date is less than or equal to
        # the input end_date, and the end_date is either greater than or equal to the
        # input start_date or is None. For example, if the input start_date is
        # '20240524' and the end_date is '20240527', the query could return an ancillary
        # file with the date range ('20240525', '20240528').
        anc_processing_inputs = []
        for input in self.job_config.inputs:
            if input.data_type == "ancillary":
                table = models.AncillaryFiles
                type_specific_conditions = []
                type_specific_conditions.append(
                    and_(
                        table.start_date < target_end,
                        or_(table.end_date >= target_start, table.end_date.is_(None)),
                    )
                )
                filter_conditions = [
                    table.instrument == input.source,
                    table.descriptor == input.descriptor,
                    *type_specific_conditions,
                ]
                max_version_query = (
                    session.query(
                        table.start_date,
                        func.max(table.version).label("latest_version"),
                    )
                    .filter(*filter_conditions)
                    .group_by(table.start_date)
                    .subquery()
                )
                # Query records
                records = (
                    session.query(table)
                    .join(
                        max_version_query,
                        (table.start_date == max_version_query.c.start_date)
                        & (table.version == max_version_query.c.latest_version),
                    )
                    .filter(*filter_conditions)
                    .all()
                )
                records = sorted(records, key=lambda x: x.start_date, reverse=True)[0:1]
                # Check requirement here if needed or not
                if not records and input.required:
                    raise MissingDependenciesError(
                        f"""Missing dependency for
                        {input.to_dagster_name()}
                        between {target_start} and {target_end}"""
                    )
                filenames = [os.path.basename(record.file_path) for record in records]
                anc_processing_inputs.append(
                    processing_input.AncillaryInput(*filenames)
                )
        return anc_processing_inputs

    def get_spin_files_inputs(
        self, session, target_start: datetime.datetime, target_end: datetime.datetime
    ) -> list[str]:
        """Return the spin file dependencies needed to cover a time range."""
        spin_files = spin.get_upstream_dependency_inputs_spin(
            target_start.replace(hour=0, minute=0, second=0), target_end, False, session
        )
        if not spin_files and self.job_config.spin_input.required:
            raise MissingDependenciesError(
                f"""Missing dependency for
                {self.job_config.spin_input.to_dagster_name()}
                between {target_start} and {target_end}"""
            )
        return spin_files

    def get_repoint_file_inputs(self, session, target_start, target_end):
        """Return the repoint file needed to cover a time range."""
        file = repoint_file.get_upstream_dependency_inputs_repoint(
            target_start, target_end, session
        )
        if not file and self.job_config.repoint_input.required:
            raise MissingDependenciesError(
                f"""Missing dependency for
                {self.job_config.repoint_input.to_dagster_name()}
                between {target_start} and {target_end}"""
            )

        return file

    def get_spice_file_inputs(self, session, target_start, target_end):
        """Return the spice files needed to cover a time range."""
        spice_files = spice.get_upstream_dependency_inputs_spice(
            self.job_config.spice_types, target_start, target_end
        )
        if not spice_files and self.job_config.spice_inputs:
            # If no SPICE files are returned, but there are SPICE inputs, raise failure
            raise MissingDependenciesError(
                f"Missing SPICE files ({', '.join(self.job_config.spice_types)}) "
                f"between {target_start} and {target_end}"
            )

        return spice_files

    def get_science_files_inputs(self, context, target_start, target_end):
        """Return the science file dependencies needed to cover a time range."""
        science_processing_inputs = []
        for input in self.job_config.science_inputs:
            dep_name = input.to_dagster_name()
            found_dep = False
            metadata_list = input.get_all_files_in_time_range(
                context, target_start, target_end
            )
            science_files = []
            for metadata in metadata_list:
                if "file_names" in metadata:
                    found_dep = (
                        True  # We can finally say we have found at least one dependency
                    )
                    # Dagster wraps metadata in a MetadataValue object,
                    # so we call .value
                    file_names = metadata["file_names"].value
                    # Handle both single strings and lists of files safely
                    if isinstance(file_names, str):
                        file_names = [file_names]
                    if file_names:
                        context.log.info(
                            f"The file names of the matching partition: {file_names}"
                        )
                    science_files.extend(file_names)

            if not found_dep and input.required:
                # If we found nothing and this is required, don't return anything.
                raise MissingDependenciesError(
                    f"""Not enough information to process.
                       Missing {dep_name} in range {target_start!s} to {target_end!s}"""
                )
            if science_files:
                pattern = re.compile(r"v(\d{3})\.(cdf|pkts)$")
                renamed_science_files = [
                    pattern.sub(r"v001.0\1.\2", file) for file in science_files
                ]
                science_processing_inputs.append(
                    processing_input.ScienceInput(*list(set(renamed_science_files)))
                )

        if not science_processing_inputs:
            # Return right away if we have zero science files.
            raise MissingDependenciesError(
                f"No science files were discovered between {target_start} and "
                f"{target_end}. All jobs require at least one science file."
            )

        return science_processing_inputs

    def get_dependencies(
        self,
        session,
        context: AssetExecutionContext,
        target_start: datetime.datetime,
        target_end: datetime.datetime,
    ):
        """Get the dependencies for the job using the DependencyResolver."""
        # Iterate through each upstream dependency
        processing_inputs = processing_input.ProcessingInputCollection()
        context.log.info(
            f"""Checking for all dependencies existing between
            {target_start} and {target_end}"""
        )

        # Get Science files
        science_files = self.get_science_files_inputs(context, target_start, target_end)
        for inputs in science_files:
            # TODO: ADD IN THE MAJOR VERSION NUMBER HANDLING WHEN CONSTRUCTING
            # ProcessingInputs.
            processing_inputs.add(inputs)

        # Get Ancillary files
        if self.job_config.ancillary_inputs:
            ancillary_files = self.get_ancillary_files_inputs(
                session, target_start, target_end
            )
            if ancillary_files:
                for inputs in ancillary_files:
                    processing_inputs.add(inputs)

        # Get the Repoint file
        if self.job_config.repoint_input:
            repoint = self.get_repoint_file_inputs(session, target_start, target_end)
            if repoint:
                processing_inputs.add(processing_input.RepointInput(repoint[0]))

        # Get SPICE files
        if self.job_config.spice_inputs:
            spice_files = self.get_spice_file_inputs(session, target_start, target_end)
            if spice_files:
                processing_inputs.add(processing_input.SPICEInput(*spice_files))

        # Get Spin files
        if self.job_config.spin_input:
            spin_files = self.get_spin_files_inputs(session, target_start, target_end)
            if spin_files:
                processing_inputs.add(processing_input.SpinInput(*spin_files))

        return processing_inputs

    def _determine_output_versions(
        self,
        session: db.Session,
        start_date: datetime,
        repointing: int | None = None,
    ) -> dict[str, dict[str, int]]:
        """Determine the major and minor version to use for each output product.

        The major version for each output comes directly from the dependency
        config and is not bumped here. The minor version is the maximum minor
        version already seen in the pipeline for this job, increased by one.

        Parameters
        ----------
        session : orm session
            Database session.
        start_date : datetime
            Start date.
        repointing : int, optional
            Repointing number. Versions are tracked independently per repointing so
            that multiple repoints on the same day each start at minor version 1.

        Returns
        -------
        dict
            Dictionary keyed by output descriptor, where each value is a dict
            with "major_version" and "minor_version" keys.
        """

        def filter_conditions(table):
            # Filter conditions for the query
            conditions = [
                table.instrument == self.job_config.source,
                table.data_level == self.job_config.data_type,
                table.descriptor == self.job_config.descriptor,
                table.start_date == start_date.date(),
                table.repointing == repointing,
            ]
            if table == models.ProcessingJob:
                conditions.append(
                    table.status.in_(
                        [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                    )
                )
            return conditions

        # Get the output nodes for the job
        job_node = (
            self.job_config.source,
            self.job_config.data_type,
            self.job_config.descriptor,
        )
        outputs = self.dependency_config_reader.config[job_node].outputs

        # Step 1: Query the processing jobs table for the most recent minor
        # version for this instrument/data_level/descriptor/start_date/repointing.
        # TODO calculate each output products minor versions independently.
        max_minor_version_record = (
            session.query(models.ProcessingJob)
            .filter(*filter_conditions(models.ProcessingJob))
            .order_by(models.ProcessingJob.minor_version.desc())
            .first()
        )
        in_progress = False
        if max_minor_version_record:
            minor_version_processing_table = max_minor_version_record.minor_version
            # Step 2: If a job for this exact key is already in progress, flag it
            # so we fall back to the processing-jobs version below instead of the
            # science files version. The minor version itself is still bumped by
            # the shared "+ 1" logic in Step 5; try_to_submit_job() is what
            # actually prevents duplicate jobs from running.
            if max_minor_version_record.status == models.Status.INPROGRESS:
                logger.info(
                    f"Job with id: {max_minor_version_record.id} is in progress, but "
                    f"the dependencies have changed. Bumping version number."
                )
                in_progress = True
        else:
            minor_version_processing_table = None

        # Spacecraft pointing-attitude jobs produce a SPICE kernel rather than a
        # science file, so there's no science files row to compare against —
        # we always need to fall back to the processing-jobs table for these.
        is_spacecraft_job = (
            self.job_config.source == "spacecraft"
            and self.job_config.descriptor == "pointing-attitude"
        )

        # Step 3: If the descriptor is "all", only use the max version from the
        # processing job table. The ScienceFiles table does not have descriptors
        # of "all", since the products produced will have their own specific
        # descriptors. Also use the processing-jobs version if this is a
        # spacecraft pointing-attitude job, or if a matching job is in progress.
        if "all" in self.job_config.descriptor or is_spacecraft_job or in_progress:
            current_minor_version = minor_version_processing_table
        else:
            # Step 4: Otherwise, get the max minor version from the science
            # files table.
            max_minor_version_sci_table = (
                session.query(func.max(models.ScienceFiles.minor_version)).filter(
                    *filter_conditions(models.ScienceFiles)
                )
            ).scalar()

            current_minor_version = max_minor_version_sci_table

        # Step 5: Bump the minor version by one (starting at 1 if no prior
        # version exists). Each output's major version comes directly from the
        # dependency config and is not bumped.
        minor_version = (
            current_minor_version + 1 if current_minor_version is not None else 1
        )
        output_versions = {}
        for output in outputs:
            output_versions[output.descriptor] = {
                "minor_version": minor_version,
                "major_version": output.major_version,
            }

        return output_versions

    def _dependency_hash(
        self, serialized_dependencies: str, output_versions: dict[str, dict[str, int]]
    ) -> str:
        """Generate a hash for the serialized dependencies.

        This is a unique ID for a particular run. This is a unique ID for a particular
        run. Dagster will refuse to run a job with the same dependency_hash. It is
        derived from the upstream dependencies, the container image hash, and the
        output products' major version numbers. Minor versions are excluded because
        they only change with a dependency update, major version bump, or code change —
        whereas major versions can change independent of those, so excluding minor lets
        us reprocess when only the major version has been bumped.

        Parameters
        ----------
        serialized_dependencies : str
            The serialized dependencies string.
        output_versions : dict[str, dict[str, int]]
            A dictionary of major and minor version numbers for each output descriptor.

        Returns
        -------
        str
            The first 8 characters of the SHA-256 hash of the serialized dependencies,
            container image digest, and output products and their major versions
             numbers
        """
        # We need to pull out the individual files and put them in alphabetical order
        dependencies = json.loads(serialized_dependencies)
        non_sclk_deps = []
        for dep in dependencies:
            for file in dep["files"]:
                if "imap_sclk" not in file and ".repoint" not in file:
                    # We'll get rid of the spacecraft_clock kernel and repoint file.
                    # These are updated frequently, and make zero
                    # difference to processing.
                    non_sclk_deps.append(file)
        # Append the image_digest
        dependency_strings = sorted(list(set(non_sclk_deps)))
        dependency_strings.append(self._get_container_image_digest())
        # Append a string of each output descriptor and its major version, sorted
        # alphabetically by descriptor
        # e.g. 'burst-magi:1,burst-mago:1,burst-raw:1,norm-magi:1,norm-mago:1'
        version_string = ",".join(
            sorted(
                [
                    f"{desc}:{val['major_version']}"
                    for desc, val in output_versions.items()
                ]
            )
        )
        dependency_strings.append(version_string)
        joined_string = "|".join(dependency_strings)

        return self._get_sha256_descriptor(joined_string)

    def _get_sha256_descriptor(self, input_string: str) -> str:
        """Generate an 8-digit hash descriptor label for a given input string.

        Parameters
        ----------
        input_string : str
            The input string to hash.

        Returns
        -------
        str
            The first 8 characters of the SHA-256 hash of the input string.
        """
        return hashlib.sha256(input_string.encode("utf-8")).hexdigest()[:8]

    def _get_container_image_digest(self):
        """Get the container image digest.

        The image digest is a sha256 hash of the image manifest, and is a unique
        identifier for the specific version of the container image used in the
        batch job. This is important for tracking which version of the code is
        being used for each job.

        Parameters
        ----------
        job_definition : str
            job definition name to get the container image digest for. For example,
            "ProcessingJob-swe"

        Returns
        -------
        str
            The sha256 digest of the image manifest. This is a unique identifier for the
            specific image version used in the batch job.

        """
        step = "-l3" if self.job_config.data_type >= "l3" else ""
        job_definition = f"ProcessingJob-{self.job_config.source}{step}"
        job_def_response = BATCH_CLIENT.describe_job_definitions(
            jobDefinitionName=job_definition, status="ACTIVE"
        )
        if not job_def_response or not job_def_response.get("jobDefinitions"):
            raise ValueError(f"Job definition not found: {job_definition}")
        # Select the latest active job definition revision.
        job_def = max(
            job_def_response["jobDefinitions"],
            key=lambda definition: definition.get("revision", 0),
        )
        container_image = job_def["containerProperties"]["image"]
        # Parse the container image URI to get the registry id, repository name
        # and image tag and use those to call describe_images and get the
        # image digest. Eg. for:
        # 123456789012.dkr.ecr.us-west-2.amazonaws.com/swapi-repo:latest
        # "123456789012" is the registry id, "swapi-repo" is the repository and
        # "latest" is the image tag.
        image_name = container_image.split("/")[-1]
        try:
            response = ECR_CLIENT.describe_images(
                registryId=container_image.split(".")[0],
                repositoryName=image_name.split(":")[0],
                imageIds=[{"imageTag": image_name.split(":")[1]}],
            )
        except ECR_CLIENT.exceptions.ImageNotFoundException as e:
            logger.error(f"Image not found in ECR for {container_image}: {e}")
            raise
        except ClientError as e:
            logger.error(f"AWS error getting image digest for {container_image}: {e}")
            raise

        # Extract the image digest from the response
        image_digest = response["imageDetails"][0]["imageDigest"]
        return image_digest

    def try_to_submit_job(
        self,
        session: db.Session,
        start_date: datetime.datetime,
        output_versions: dict,
        serialized_dependencies: str,
        repoint: int | None = None,
    ):
        """Try to submit a batch job with the given job information.

        Parameters
        ----------
        session : orm session
            Database session.
        start_date : datetime.datetime
            Start date of the data in the format 'YYYYMMDD'.
        output_versions : dict
            Dictionary keyed by output descriptor, where each value is a dict with
             "major_version" and "minor_version" keys.
        serialized_dependencies : str
            The serialized ProcessingInputCollection of the upstream
            dependencies.
        repoint : int, optional
            The repointing number for the job, if applicable. Default is None. Should
            be just an integer, no "repoint" prefix.
        """
        # This job may produce multiple outputs, each with its own major/minor
        # version (see output_versions). Take the max major and max minor version
        # across all outputs to use as a single label for the dependency file name
        # and the processing job table row. The actual output product files will
        # each use their own specific version from the output_versions dictionary,
        # not this combined max.
        max_major_version = max([v["major_version"] for v in output_versions.values()])
        max_minor_version = max([v["minor_version"] for v in output_versions.values()])

        # Calculate the dependency hash, if dependencies
        # change, the hash changes. Combined with the unique constraint on
        # (dependency_hash, container_image_digest), this gives us duplicate detection:
        # same deps + same digest = IntegrityError = job skipped
        # For a given instrument, data_level, start_date ect. If either the deps change
        # or the image changes then a new job is allowed with a bumped version number.
        start_date_str = start_date.strftime("%Y%m%d")

        # Combine the per-output version info and the upstream dependencies into a
        # single JSON payload. This is written to a dependency file and uploaded;
        # the Imap processing code reads the file and deserializes it, rather than
        # passing a large string through the batch job command line.
        serialized_processing_info = json.dumps(
            {
                "dependency": json.loads(serialized_dependencies),
                "version": output_versions,
            }
        )
        # Add the dependency hash and version hash to the dependency file name to ensure
        # uniqueness. The dependency hash is based on the upstream dependencies and the
        # container image digest. The version hash is based on the output versions.
        dep_hash = self._dependency_hash(serialized_dependencies, output_versions)
        version_hash = self._get_sha256_descriptor(json.dumps(output_versions))
        dep_descriptor = f"{self.job_config.descriptor}-{dep_hash}-{version_hash}"
        dependency_file = DependencyFilePath.generate_from_inputs(
            instrument=self.job_config.source,
            data_level=self.job_config.data_type,
            descriptor=dep_descriptor,
            start_time=start_date_str,
            major_version=max_major_version,
            minor_version=max_minor_version,
            extension="json",
            repointing=repoint,
        )
        dependency_file_path = dependency_file.construct_path()
        response = self.upload_dependency_file(
            dependency_file_path, serialized_processing_info
        )
        # If response is None,
        # the upload failed and we should skip submitting the job.
        if not response:
            return BatchJobSubmit(
                status="failed", message="Dependency JSON file upload failed."
            )

        batch_command = [
            "--instrument",
            self.job_config.source,
            "--data-level",
            self.job_config.data_type,
            "--descriptor",
            self.job_config.descriptor,
            "--start-date",
            start_date_str,
            "--dependency",
            dependency_file_path.name,
            "--upload-to-sdc",
        ]

        if repoint is not None:
            batch_command.extend(["--repointing", f"repoint{repoint:05d}"])

        # Get the necessary AWS information
        # NOTE: These are here for easier mocking in tests rather than at the
        # module level
        step = "-l3" if self.job_config.data_type >= "l3" else ""
        job_definition = f"ProcessingJob-{self.job_config.source}{step}"

        # Capture the container image and digest right before submitting the job.
        # This ensures the image digest that will be used is recorded. We record this
        # information here and not in indexer.py to avoid race conditions where the
        # image could change during job execution.
        container_image_digest = self._get_container_image_digest()

        # All of our upstream requirements have been met.
        # Try to insert a record into the Processing Jobs table
        # If this job already exists, then we will get an integrity error
        # and know that some other process has already taken care of it
        processing_job = models.ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument=self.job_config.source,
            data_level=self.job_config.data_type,
            descriptor=self.job_config.descriptor,
            start_date=datetime.datetime.strptime(start_date_str, "%Y%m%d"),
            major_version=max_major_version,
            minor_version=max_minor_version,
            repointing=repoint,
            dependency_hash=dep_hash,
            container_command=" ".join(batch_command),
            container_image_digest=container_image_digest,
        )
        try:
            session.add(processing_job)
            session.commit()
        except IntegrityError:
            # Rollback the session to clear the failed transaction
            session.rollback()
            logger.info(
                f"Job already completed or in progress. Tried to submit "
                f"{processing_job.to_dict()}"
            )
            return BatchJobSubmit(
                status="skipped",
                message="Job already completed or in progress.",
                job=processing_job.to_dict(),
            )
        # NOTE: The batch job name should contain only alphanumeric characters
        # and hyphens
        # E.g. "codice-l1a-sci-job-1"
        # The `processing_job.id` is used later for updating the job processing table
        job_name = (
            f"{self.job_config.source}-"
            f"{self.job_config.data_type}-"
            f"{self.job_config.descriptor}-"
            f"job-{processing_job.id}"
        )
        job_queue = "ProcessingJobQueue"

        response = BATCH_CLIENT.submit_job(
            jobName=job_name,
            jobQueue=job_queue,
            jobDefinition=job_definition,
            containerOverrides={
                "command": batch_command,
            },
            retryStrategy=BATCH_JOB_RETRY_STRATEGY,
        )

        logger.info(f"Submitted job {job_name} with this command: {batch_command}")
        return BatchJobSubmit(
            status="submitted",
            message="Job submitted successfully.",
            job=processing_job.to_dict(),
            response=response,
        )

    def upload_dependency_file(
        self, dependency_file_path: Path, serialized_dependencies: str
    ):
        """Upload a JSON file containing a job's dependencies to S3.

        Parameters
        ----------
        dependency_file_path : Path
            The dependency JSON file to upload.
        serialized_dependencies : str
            The serialized upstream dependencies to upload.
        """
        # Check if the file already exists
        if os.path.isfile(dependency_file_path):
            raise KeyError(
                f"{dependency_file_path} already exists, cannot create JSON file."
            )
        # call the upload API handler directly
        signed_url = upload_api.lambda_handler(
            {
                "pathParameters": {"proxy": dependency_file_path.as_posix()},
                "requestContext": {
                    "authorizer": {
                        "lambda": {"scope": "write", "apiKey": "batch-starter"}
                    }
                },
            },
            None,
        )
        if signed_url["statusCode"] == 409:
            logger.info(
                f"Dependency file already exists in S3: {dependency_file_path}. Reusing"
                f"file."
            )
            return {"statusCode": 200, "body": signed_url["body"]}
        elif signed_url["statusCode"] != 200:
            logger.error(
                f"Failed to get S3 pre-signed URL for file: {dependency_file_path}. "
                f"As a result, failed to kick off job. "
                f"Error message: {signed_url['body']}, "
                f"with status code: {signed_url['statusCode']}."
            )
            return None
        try:
            response = requests.put(
                signed_url["body"].strip('"'),
                data=serialized_dependencies,
                headers={"Content-Type": "application/json"},
                timeout=60.0,
            )
            logger.info(
                f"Dependency file uploaded successfully to s3 with status code: "
                f"{response.status_code}"
            )
            return response
        except Exception as e:
            logger.error(
                f"Unexpected error during cadence file upload: {e}. "
                f"Dependency file upload failed and the job did not get kicked off."
            )
            return None
