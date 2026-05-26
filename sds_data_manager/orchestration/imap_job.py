"""IMAP job handler for managing dependencies and job submission."""

import datetime
import hashlib
import json
import logging
import os
import re
import time

import boto3
import imap_data_access
from botocore.exceptions import ClientError
from dagster import (
    AssetExecutionContext,
    AssetOut,
    DagsterEventType,
    DefaultSensorStatus,
    EventRecordsFilter,
    Failure,
    RunRequest,
    SensorEvaluationContext,
    define_asset_job,
    multi_asset,
    sensor,
)
from imap_data_access import processing_input
from imap_data_access.io import download
from sqlalchemy import func

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import custom_partitions
from sds_data_manager.orchestration.dagster_utilities import get_materialization_result
from sds_data_manager.orchestration.types import DependencyNode, ProcessingJobNode

BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")
# Create an ECR client for getting container image digests
ECR_CLIENT = boto3.client("ecr", region_name="us-west-2")
# Define the retry strategy for batch jobs
BATCH_JOB_RETRY_STRATEGY = {
    "attempts": 10,
    "evaluateOnExit": [
        {
            "onStatusReason": "Your Spot Task was interrupted.",
            "action": "RETRY",
        },
        {"onReason": "*", "action": "EXIT"},
    ],
}
# Create an sqs client
SQS_CLIENT = boto3.client("sqs", region_name="us-west-2")


# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

priority_levels = {
    "l0": "0",
    "l1a": "-1",
    "l1b": "-2",
    "l1c": "-3",
    "l1d": "-4",
    "l2": "-5",
    "l2a": "-6",
    "l2b": "-7",
    "l2c": "-8",
    "l2d": "-9",
    "l3": "-10",
    "l3a": "-11",
    "l3b": "-12",
    "l3c": "-13",
    "l3d": "-14",
}

_sensor_schedule = {
    "l0": 300,
    "l1a": 300,
    "l1b": 300,
    "l1c": 300,
    "l1d": 300,
    "l2": 300,
    "l2a": 300,
    "l2b": 300,
    "l2c": 300,
    "l2d": 300,
    "l3": 300,
    "l3a": 300,
    "l3b": 300,
    "l3c": 300,
    "l3d": 300,
}

partition_map = {
    "daily": custom_partitions.daily_partitions,
    "repoint": custom_partitions.repoint_partitions,
    "10d": custom_partitions.idex10_partitions,
    # NOTE: Right now, IDEX is the only instrument who uses 1mo cadence job that
    # maps to exactly 30 days. If this changes, this logic will need update.
    "30d": custom_partitions.idex30_partitions,
    # TODO: add cadence custom partition definition and update to use those
    # later
    "3mo": custom_partitions.idex30_partitions,
    "6mo": custom_partitions.idex30_partitions,
    "1yr": custom_partitions.whole_mission_partition,
}


class IMAPJobHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, job: ProcessingJobNode):
        """Initialize handler with job node and process dependencies.

        Parameters
        ----------
        job : ProcessingJobNode
            The job node to process.
        """
        self.job_config = job

        self.partitions_def = partition_map.get(self.job_config.partition)
        self.sensor_schedule = _sensor_schedule.get(self.job_config.data_type, 600)

        outputs_for_job = [x.to_dagster_asset() for x in self.job_config.outputs]

        self.dagster_job = define_asset_job(
            name=f"{self.job_config.to_dagster_asset().to_user_string()}_processing_job",
            selection=outputs_for_job,
            tags={
                "dagster/priority": priority_levels.get(self.job_config.data_type, "0")
            },
        )

    def build_asset(self):
        """This builds the Asset for Dagster for a particular job. This function will:

        1) Get all dependencies from the dependency tree
        2) Check if the job had been submitted before
           a) If it has, and Dagster doesn't know about it, then it will materialize the asset
           b) If Dagster does know about it, we exit
        3) Get the Job version
        4) Submit the job
        5) Wait for the output files, and materialize them as we see them in the database.

        """
        input_keys = [dep.to_dagster_asset() for dep in self.job_config.inputs]
        output_assets = {}
        for out in self.job_config.outputs:
            output_assets[out.to_dagster_asset().to_user_string()] = AssetOut(
                is_required=False
            )

        @multi_asset(
            name=f"{self.job_config.to_dagster_asset().to_user_string()}_multi_asset_op",
            deps=input_keys,
            partitions_def=self.partitions_def,
            outs=output_assets,
        )
        def _generic_batch_submitter(context: AssetExecutionContext):
            parts = context.partition_key.split("_")
            start_date = (
                datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=datetime.timezone.utc)
                .date()
            )
            if "repoint" in parts[0]:
                pointing_number = int(parts[0][7:])
            else:
                pointing_number = None

            # 1. Figure out what time window this specific run is responsible for
            target_partition = context.partition_key
            target_start, target_end = self._parse_dates_from_key(target_partition)

            # 2. Get Dependencies
            dependency_inputs = self.get_dependencies(context, target_start, target_end)
            if not dependency_inputs:
                raise Failure(
                    description="Processing failed: Dependency inputs were missing."
                )
            already_processed = False
            with db.Session() as session:
                for output in self.job_config.outputs:
                    previous_file = self.is_duplicate_job(
                        context,
                        session,
                        dependency_inputs,
                        self.job_config.source,
                        self.job_config.data_type,
                        output.descriptor,
                        self.job_config.descriptor,
                        start_date,
                        pointing_number,
                    )

                    if previous_file:
                        already_processed = True
                        materialization = get_materialization_result(
                            context,
                            output.to_dagster_asset(),
                            context.partition_key,
                            [os.path.basename(previous_file.file_path)],
                            previous_file.version,
                            "science",
                            inputs=dependency_inputs.serialize(),
                        )
                        if materialization:
                            yield materialization

                if not already_processed:
                    job_version = self._determine_job_version(
                        session=session,
                        instrument=self.job_config.source,
                        descriptor=self.job_config.descriptor,
                        start_date=start_date,
                        data_level=self.job_config.data_type,
                        current_dependencies=dependency_inputs.serialize(),
                    )
                    context.log.info(f"Job Version to Use: {job_version}")

                    """
                    # SUBMIT JOB HERE!!!
                    job_node = {"data_source":instrument,
                                "data_type":level,
                                "descriptor":descriptor}
                    submit_response = batch_starter.try_to_submit_job(
                                                                        session,
                                                                        job_node,
                                                                        start_date,
                                                                        job_version,
                                                                        dependency_inputs.serialize(),
                                                                        repoint=int(target_partition),
                                                                    )
                    """
                    # if submit_status.status == 'submitted':
                    for output in self.job_config.outputs:
                        context.log.info(
                            f"Waiting for data product {output.source}_{output.data_type}_{output.descriptor} to complete"
                        )
                        file = self.wait_for_file(
                            context,
                            session,
                            output,
                            job_version,
                            repointing=pointing_number,
                            start_date=start_date.strftime("%Y%m%d"),
                            inputs=dependency_inputs.serialize(),
                        )

                        if file:
                            yield file
                        # TODO: We should not yield right away. We should collect up anything that this asset has generated,
                        # yield what we can, and determine if anything required in the output is missing, then yield a failure.

        # Return the generated function back to Dagster
        return _generic_batch_submitter

    def wait_for_file(
        self,
        context,
        session: db.Session,
        output: DependencyNode,
        job_version: str,
        start_date: str = None,
        repointing: int = None,
        inputs={},
    ):
        if start_date is None and repointing is None:
            raise ValueError(
                "You must at least provide either start_date or repointing"
            )

        parsed_start_date = None
        if start_date is not None:
            parsed_start_date = datetime.datetime.strptime(start_date, "%Y%m%d")

        timeout = (
            3  # TODO: Set this for WAY higher once we're actually waiting for files.
        )
        timeout_start = time.time()
        while time.time() < timeout_start + timeout:
            filters = [
                models.ScienceFiles.instrument == output.source,
                models.ScienceFiles.data_level == output.data_type,
                models.ScienceFiles.descriptor == output.descriptor,
                models.ScienceFiles.version == job_version,
            ]
            if repointing is not None:
                filters.append(models.ScienceFiles.repointing == int(repointing))
            if parsed_start_date is not None:
                filters.append(models.ScienceFiles.start_date == parsed_start_date)
            created_file = session.query(models.ScienceFiles).filter(*filters).first()
            if created_file:
                context.log.info(
                    f"Found file {os.path.basename(created_file.file_path)}! Creating Asset."
                )
                materialization = get_materialization_result(
                    context,
                    output.to_dagster_asset(),
                    context.partition_key,
                    [os.path.basename(created_file.file_path)],
                    str(int(job_version[1:])),
                    "science",
                    inputs=inputs,
                )
                if materialization:
                    return materialization
            time.sleep(
                1
            )  # TODO: Set this for WAY higher once we're actually waiting for files.

    def _parse_dates_from_key(
        self, partition_key: str
    ) -> tuple[datetime.datetime, datetime.datetime]:
        """Extracts start and end datetimes from a string formatted like:
        '{name}_%Y-%m-%dT%H:%M:%S_to_%Y-%m-%dT%H:%M:%S'
        """
        if not partition_key:
            return None, None

        date_range = partition_key.split("_", 1)[1]
        if "_to_" in date_range:
            p_start_str, p_end_str = date_range.split("_to_")
            p_start = datetime.datetime.strptime(
                p_start_str, "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=datetime.timezone.utc)
            p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )

        return p_start, p_end

    def _get_overlapping_target_partitions(
        self, upstream_partition_key, up_start, up_end, instance
    ):
        """Evaluates the upstream date range against existing downstream partitions.
        Returns a list of downstream partition keys that overlap.
        """
        target_keys = []
        # Fetch the currently known dynamic partitions for your target asset
        existing_downstream_keys = instance.get_dynamic_partitions(
            self.partitions_def.name
        )

        # Check if the upstream and downstream are in the same partition
        if upstream_partition_key in existing_downstream_keys:
            # These are actually in same partition! No need to loop through the other keys.
            return [upstream_partition_key]

        for down_key in existing_downstream_keys:
            down_start, down_end = self._parse_dates_from_key(down_key)
            if not down_start or not down_end:
                continue

            # Math logic for overlapping date intervals
            if up_start <= down_end and up_end >= down_start:
                target_keys.append(down_key)

        return target_keys

    def build_sensor(self):
        """Factory function that returns a Dagster sensor monitoring the dependencies
        and triggering this asset when overlapping data arrives.

        1) Checks for all of the latest asset materializations in dagster for all dependencies of this product
        2) Loops through the latest materializations
        3) Determines what time range those materializations belong to
        4) Determines what partition keys of this asset those new materializations belong to
        5) For each affected partition, we determine the dependencies. If we have all dependencies, we are good!
        6) Yield a RunRequest for a job to make the asset for the partitions that have all dependencies.
        """
        deps_keys = [x.to_dagster_asset() for x in self.job_config.triggering_deps]
        sensor_name = (
            f"{self.job_config.to_dagster_asset().to_user_string()}_kickoff_sensor"
        )

        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=self.sensor_schedule,
            default_status=DefaultSensorStatus.RUNNING,
        )
        def _sensor(context: SensorEvaluationContext):

            # Load the cursor to track which events we have already seen.
            # The cursor maps `{asset_name: last_processed_storage_id}`.
            cursors = json.loads(context.cursor) if context.cursor else {}
            new_cursors = cursors.copy()

            # Iterate through each dependency to find unconsumed materializations
            for dep_key in deps_keys:
                dep_name = dep_key.to_user_string()
                context.log.info(f"Checking new dependencies for: {dep_name}")

                # Fetch the last evaluated event ID for this specific dependency
                last_event_id = cursors.get(dep_name)
                filter = EventRecordsFilter(
                    event_type=DagsterEventType.ASSET_MATERIALIZATION,
                    asset_key=dep_key,
                    after_cursor=last_event_id,
                )
                new_events = context.instance.get_event_records(filter, limit=1)
                if new_events:
                    record = new_events[0]
                else:
                    continue

                # Update our new cursor marker to the highest storage ID seen
                new_cursors[dep_name] = record.storage_id

                upstream_partition_key = record.event_log_entry.dagster_event.partition
                up_start, up_end = self._parse_dates_from_key(upstream_partition_key)

                if not up_start or not up_end:
                    continue

                # Calculate overlap
                target_partitions = self._get_overlapping_target_partitions(
                    upstream_partition_key, up_start, up_end, context.instance
                )

                for target_partition in target_partitions:
                    # Queue the RunRequest
                    target_start, target_end = self._parse_dates_from_key(
                        target_partition
                    )
                    dependencies = self.get_dependencies(
                        context, target_start, target_end
                    )
                    if dependencies:
                        tags = {"dependencies": dependencies.serialize()}
                        yield RunRequest(
                            partition_key=target_partition,
                            run_key=f"{self.job_config.to_dagster_asset().to_user_string()}_{target_partition}_{self._dependency_hash(dependencies.serialize())}",
                            tags=tags,
                        )
                break  # To keep the sensor short, we'll only analyze one new dependency at a time. If there are still new ones, we'll consume them next time.
                # The get_dependencies will still get the latest stuff regardless, so we're never submitting outdated data.

            # 5. Lock in the new cursor state and execute
            context.update_cursor(json.dumps(new_cursors))

        return _sensor

    def process_job(self, potential_job_node: DependencyNode):
        """Process the job by resolving dependencies and submitting to batch."""
        if self.dependencies is not None:
            self._calculate_crid()
            self._determine_job_version()
            # TODO: uncomment these lines at implementation time
            # -----------------------------------------------------
            # job_dependencies_s3_filepath = self._create_dependencies_file()
            # dependency_serialized_hash = dependency_hash(self.dependencies)
            # is_duplicate_job = self.is_duplicate_job(
            #     potential_job_node, dependency_serialized_hash
            # )
            # if not is_duplicate_job:
            #     upload_response = upload_dependency_file(
            #         self.dependencies, job_dependencies_s3_filepath
            #     )
            #     if upload_response["status"] != 200:
            #         raise Exception("Failed to upload dependency file to S3.")

            #     job_submit_succeed = self.submit_processing_job(
            #         job_dependencies_s3_filepath
            #     )
            #     if job_submit_succeed:
            #         self.clean_up()

    def get_dependencies(
        self,
        context: AssetExecutionContext,
        target_start: datetime.datetime,
        target_end: datetime.datetime,
    ):
        """Get the dependencies for the job using the DependencyResolver."""
        # Iterate through each upstream dependency
        dependency_inputs = processing_input.ProcessingInputCollection()
        context.log.info(
            f"Checking for all dependencies existing between {target_start} and {target_end}"
        )

        for input in self.job_config.inputs:
            dep_name = input.to_user_string()
            found_dep = False
            context.log.info(f"Checking out {dep_name}")
            metadata_list = input.get_all_files_in_time_range()
            for metadata in metadata_list:
                if "file_names" in metadata:
                    found_dep = (
                        True  # We can finally say we have found at least one dependency
                    )
                    # Dagster wraps metadata in a MetadataValue object, so we call .value
                    file_names = metadata["file_names"].value
                    # Handle both single strings and lists of files safely
                    if file_names:
                        context.log.info(
                            f"The file names of the matching partition: {file_names}"
                        )
                    input_type = metadata["input_type"].value
                    if input_type == "science":
                        dependency_inputs.add(
                            processing_input.ScienceInput(*file_names)
                        )
                    if input_type == "ancillary":
                        dependency_inputs.add(
                            processing_input.AncillaryInput(*file_names)
                        )
                    if input_type == "spin":
                        dependency_inputs.add(processing_input.SpinInput(*file_names))
                    if input_type == "repoint":
                        dependency_inputs.add(
                            processing_input.RepointInput(file_names[0])
                        )
                    if input_type == "spice":
                        dependency_inputs.add(processing_input.SPICEInput(*file_names))
            if not found_dep:
                # TODO: Check here if we have a soft dependency.
                context.log.info(
                    f"Not enought information to process. Missing {dep_name} in range {target_start!s} to {target_end!s}"
                )
                return

        return dependency_inputs

    def is_duplicate_job(
        self,
        context,
        session,
        dependency,
        instrument,
        level,
        descriptor,
        job_command,
        start_date,
        repoint,
    ):
        """Determine if the job is a duplicate.

        Requirements for duplicate job determination:
            1. Must be unique dependency serialized hash AND
            2. AWS ECR container image digest hash must be unique AND
            3. Potential job node's must be unique AND
            4. Job status must be either INPROGRESS or SUCCEEDED.
        """
        # 1. Get AWS ECR container image digest hash, container_image_digest.
        #    This should unique.
        # 2. Now query DB with these inputs and we will know if a job is duplicate.
        #   max_version_record = (
        #     session.query(models.ProcessingJob)
        #     .filter(table.instrument == potential_job_node.instrument,
        #             table.data_level == potential_job_node.data_level,
        #             table.descriptor == potential_job_node.descriptor,
        #             table.start_date == potential_job_node.start_date,
        #             table.repoint == potential_job_node.repoint,
        #             table.dependency_hash == serialized_dependency_hash,
        #             table.contianer_image_digest == container_image_digest,
        #             table.status.in_(
        #                 [models.Status.INPROGRESS.value,
        #                   models.Status.SUCCEEDED.value]
        #             )
        #             )
        #     .order_by(models.ProcessingJob.version.desc())
        #     .first()
        # )
        # 3. If return exists, it's a duplicate job and return True.

        """
        This function will check if the latest file at the SDC has already been processed using
        the gathered dependencies. To do this, it:

        1) Finds the latest file
        2) Finds the corresponding Processing Job
        3) Read in the .json dependency file from the corresponding Processing Job
        4) Compares the Ancillary and Science files in the dependency file to the current ones
        5) If they all match, the function returns the materialization result of the latest file

        Yes, this is somewhat complicated right now. I think the best way going forward would be to
        add the dependencies to a particular file either in the ScienceFiles or ProcessingJob tables.
        """
        context.log.info(
            f"Checking if we have already have a file of this instrument of {instrument}, this level of {level}, and this {descriptor}."
        )
        context.log.info(
            f"And this start date {start_date}, and this pointing number {repoint}."
        )
        if repoint:
            latest_file = (
                session.query(models.ScienceFiles)
                .filter(
                    models.ScienceFiles.instrument == instrument,
                    models.ScienceFiles.data_level == level,
                    models.ScienceFiles.descriptor == descriptor,
                    models.ScienceFiles.repointing == int(repoint),
                )
                .order_by(models.ScienceFiles.version.desc())
                .first()
            )
        else:
            latest_file = (
                session.query(models.ScienceFiles)
                .filter(
                    models.ScienceFiles.instrument == instrument,
                    models.ScienceFiles.data_level == level,
                    models.ScienceFiles.descriptor == descriptor,
                    models.ScienceFiles.start_date == start_date,
                )
                .order_by(models.ScienceFiles.version.desc())
                .first()
            )
        if latest_file:
            context.log.info(
                "Yes we did! Now we need to check the processing jobs to see if the same dependencies were used to create this. "
            )
            max_version_record = (
                session.query(models.ProcessingJob)
                .filter(
                    models.ProcessingJob.instrument == latest_file.instrument,
                    models.ProcessingJob.data_level == latest_file.data_level,
                    models.ProcessingJob.descriptor == job_command,
                    models.ProcessingJob.start_date == latest_file.start_date,
                    models.ProcessingJob.status.in_(
                        [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                    ),
                )
                .order_by(models.ProcessingJob.version.desc())
                .first()
            )
            if max_version_record:
                context.log.info(
                    "A job does indeed look like this one...lets check out its dependencies file"
                )
                match = re.search(
                    r"--dependency\s+(\S+\.json)", max_version_record.container_command
                )
                if match:
                    dependency_file = match.group(1)
                    context.log.info(dependency_file)
                    SSM_CLIENT = boto3.client("ssm")
                    ssm_parameter_name = os.environ.get(
                        "SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key"
                    )
                    try:
                        ssm_response = SSM_CLIENT.get_parameter(
                            Name=ssm_parameter_name, WithDecryption=True
                        )
                        imap_data_access.config["API_KEY"] = ssm_response["Parameter"][
                            "Value"
                        ]
                    except Exception as e:
                        print(f"Could not retrieve API key from SSM: {e}")
                    dependency_filepath = download(dependency_file)
                    with open(dependency_filepath) as f:
                        old_inputs = json.loads(f.read())
                    context.log.info(
                        f"Check it out! These are the dependencies it previously ran with: {json.dumps(old_inputs)}"
                    )
                    dependencies_match = True
                    old_science_inputs = []
                    old_ancillary_inputs = []
                    new_science_inputs = []
                    new_ancillary_inputs = []
                    for dep in old_inputs:
                        if dep["type"] == "science":
                            old_science_inputs.extend(dep["files"])
                        if dep["type"] == "ancillary":
                            old_ancillary_inputs.extend(dep["files"])
                    new_inputs = json.loads(dependency.serialize())
                    context.log.info(
                        f"Check it out! These are the dependencies that we want to run it with now: {json.dumps(new_inputs)}"
                    )
                    for dep in new_inputs:
                        if dep["type"] == "science":
                            new_science_inputs.extend(dep["files"])
                        if dep["type"] == "ancillary":
                            new_ancillary_inputs.extend(dep["files"])

                    if set(old_science_inputs) != set(new_science_inputs):
                        dependencies_match = False
                    if set(old_ancillary_inputs) != set(new_ancillary_inputs):
                        dependencies_match = False

                    if dependencies_match:
                        context.log.info("It's a match!")
                        # The latest file does not need to be updated.
                        # We need to tell dagster that this asset is complete.
                        context.log.info(f"Latest file: {latest_file.file_path}")
                        context.log.info(f"Version: {latest_file.version}")
                        return latest_file
                    else:
                        context.log.info("It's not a match!")
        else:
            context.log.info("No files have ever been made like this before!")

    def _determine_job_version(
        self,
        session: db.Session,
        instrument: str,
        data_level: str,
        descriptor: str,
        start_date: datetime,
        current_dependencies: str,
    ) -> str:
        """Return the maximum existing file version in the pipeline increased by one.

        Parameters
        ----------
        session : orm session
            Database session.
        instrument : str
            Instrument.
        data_level : str
            Data level.
        descriptor : str
            Data descriptor.
        start_date : datetime
            Start date.
        current_dependencies : str
            Serialized dependencies for the current job.

        Returns
        -------
        str
            The highest version number.
        """

        def filter_conditions(table):
            # Filter conditions for the query
            conditions = [
                table.instrument == instrument,
                table.data_level == data_level,
                table.descriptor == descriptor,
                table.start_date == start_date,
            ]
            if table == models.ProcessingJob:
                conditions.append(
                    table.status.in_(
                        [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                    )
                )
            return conditions

        # Step 1: query to get the max version from the processing jobs table
        max_version_record = (
            session.query(models.ProcessingJob)
            .filter(*filter_conditions(models.ProcessingJob))
            .order_by(models.ProcessingJob.version.desc())
            .first()
        )
        if max_version_record:
            max_version_processing = max_version_record.version
            # Step 2: If there is a job already in progress, determine whether the current
            # job is a duplicate of the in-progress job by checking the dependency file
            # hash. If the hashes are different, then we know the dependencies have changed
            # and we should bump the version number and continue with processing.
            if max_version_record.status == models.Status.INPROGRESS:
                command = max_version_record.container_command
                if self._dependency_hash(current_dependencies) in command:
                    # Return the current max version and this job will not proceed if
                    # everything else is the same.
                    return max_version_processing
                else:
                    # Dependencies have changed, so bump the version number.
                    logger.info(
                        f"Job with id: {max_version_record.id} is in progress, but the "
                        f"dependencies have changed. Bumping version number."
                    )
                    return f"v{int(max_version_processing[1:]) + 1:03d}"

        else:
            max_version_processing = None
        # Step 3: If the descriptor is "all", only use the max version from the processing
        # job table. The ScienceFiles table does not have descriptors of "all" since the
        # products produced will have their own specific descriptors.
        if "all" in descriptor:
            return (
                f"v{int(max_version_processing[1:]) + 1:03d}"
                if max_version_processing
                else "v001"
            )

        # Step 4: Get the max version from the science files table.
        max_version_sci = (
            session.query(func.max(models.ScienceFiles.version)).filter(
                *filter_conditions(models.ScienceFiles)
            )
        ).scalar()

        # Step 5: By default, use the max version from the science files table unless
        # it is a spacecraft "pointing-attitude" job. If a so, then use the max version
        # from the processing jobs table. If the job is a spacecraft pointing-attitude job,
        # it will produce a SPICE kernel and not a science file. There is no way to
        # determine the filename of the kernel that will be produced, so we rely on the max
        # version from the processing jobs table.
        if instrument == "spacecraft" and descriptor == "pointing-attitude":
            max_version = max_version_processing
        else:
            max_version = max_version_sci

        # Bump the version number. "V001" will be returned if max_version is None.
        return f"v{int(max_version[1:]) + 1:03d}" if max_version else "v001"

    def _dependency_hash(self, serialized_dependencies):
        """Generate a hash for the serialized dependencies. Use only the first 8 characters.

        This is a unique ID for a particular run. Dagster will refused to run a job with
        the same dependency_hash.

        Parameters
        ----------
        serialized_dependencies : str
            The serialized dependencies string.

        Returns
        -------
        str
            The first 8 characters of the SHA-256 hash of the serialized dependencies.
        """
        # We need to pull out the individual files and put them in alphabetical order
        dependencies = json.loads(serialized_dependencies)
        non_sclk_deps = []
        for dep in dependencies:
            for file in dep["files"]:
                if "imap_sclk" not in file:
                    # We'll get rid of the spacecraft_clock kernel, if it exists
                    non_sclk_deps.append(file)
        # Append the image_digest
        sorted_files = sorted(non_sclk_deps)
        sorted_files.append(self._get_container_image_digest())
        joined_string = "|".join(sorted_files)

        return hashlib.sha256(joined_string.encode("utf-8")).hexdigest()[:8]

    def _get_container_image_digest(self):
        """Get the container image digest.

        The image digest is a sha256 hash of the image manifest and is a unique
        identifier for the specific version of the container image used in the batch
        job. This is important for tracking which version of the code is being used
        for each job.

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
        # Parse the container image URI to get the registry id, repository name and
        # image tag and use those to call describe_images and get the image digest.
        # Eg. for 123456789012.dkr.ecr.us-west-2.amazonaws.com/swapi-repo:latest,
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

    def _create_dependencies_file(self):
        """Create and upload a dependency json file to S3 for the job.

        This file is a json file containting serialized output of upstream
        dependencies and information needed for IMAP job command line input (CLI).
        """
        # TODO: Remove information not needed for IMAP CLI input from
        # self.potential_job_node
        # cli_input = self.potential_job_node

        # TODO: convert start_date and end_date to string and format needed
        # for CLI input. Eg. "yyyymmdd"

        # upstream_dependency_content = self.dependencies
        # TODO: write to dependency json file.
        dependency_file_path = "/some/path/dependency_file.json"
        return dependency_file_path

    def submit_processing_job(self, job_dependencies_s3_filepath: str):
        """Submit AWS batch processing job with dependencies and inputs.

        Return:
        ------
        bool
            True if job is submitted successfully, False otherwise.
        """
        # Finally, in this function, submit job to batch job with CLI
        # input of self.dependency_s3_path
        return True

    def clean_up(self):
        """Clean up resources or temporary files used during job processing."""
        # clean up any resources or temporary files used during the job.
        # Eg. right now, we clean up SQS queue if job is submitted successfully.
        pass
