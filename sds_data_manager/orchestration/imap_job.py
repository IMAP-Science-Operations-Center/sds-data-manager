"""IMAP job handler for managing dependencies and job submission."""

import json
import datetime
import time
import boto3
import os
import logging
import hashlib
import requests
from dataclasses import dataclass
from collections import defaultdict
from dagster import (
    AssetExecutionContext,
    Failure,
    SensorEvaluationContext,
    sensor,
    EventRecordsFilter,
    DagsterEventType,
    RunRequest,
    DefaultSensorStatus,
    multi_asset,
    AssetOut,
    define_asset_job,
    RunsFilter,
    SkipReason
)
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.types import DependencyNode, ProcessingJobNode
from sds_data_manager.orchestration import custom_partitions
from sds_data_manager.orchestration.dagster_utilities import get_materialization_result
from sds_data_manager.lambda_code.SDSCode.api_lambdas import upload_api
from imap_data_access import processing_input, DependencyFilePath
import boto3
from botocore.exceptions import ClientError
from sqlalchemy import func
from pathlib import Path
from sqlalchemy.exc import IntegrityError

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

priority_levels = {'l0':'0',
                   'l1': -1,
                   'l1a':'-2',
                   'l1b':'-3',
                   'l1c':'-4',
                   'l1d':'-5',
                   'l2':'-6',
                   'l2a':'-7',
                   'l2b':'-8',
                   'l2c':'-9',
                   'l2d':'-10',
                   'l3':'-11',
                   'l3a':'-12',
                   'l3b':'-13',
                   'l3c':'-14',
                   'l3d':'-15',}

_sensor_schedule = {'l0': 300,
                    'l1': 300,
                    'l1a':300,
                    'l1b':300,
                    'l1c':300,
                    'l1d':300,
                    'l2': 300,
                    'l2a':300,
                    'l2b':300,
                    'l2c':300,
                    'l2d':300,
                    'l3': 300,
                    'l3a':300,
                    'l3b':300,
                    'l3c':300,
                    'l3d':300}

partition_map = {
            "daily":   custom_partitions.daily_partitions,
            "repoint": custom_partitions.repoint_partitions,
            "10d":     custom_partitions.idex10_partitions,
            # NOTE: Right now, IDEX is the only instrument who uses 1mo cadence job that
            # maps to exactly 30 days. If this changes, this logic will need update.
            "30d":     custom_partitions.idex30_partitions,
            # TODO: add cadence custom partition definition and update to use those
            # later
            "3mo":     custom_partitions.idex30_partitions,
            "6mo":     custom_partitions.idex30_partitions,
            "1yr":     custom_partitions.whole_mission_partition,
        }

@dataclass
class BatchJobSubmit:
    """Class to store information about a batch job submission."""
    status: str
    message: str
    # the ProcessingJob information as a dictionary.
    job: dict | None = None

class IMAPJobHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, 
                 job: ProcessingJobNode):
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
        self.dagster_job_name = f"{self.job_config.to_dagster_asset().to_user_string()}_processing_job"
        self.dagster_job = define_asset_job(name=self.dagster_job_name,
                                           selection=outputs_for_job,
                                           tags={"dagster/priority": priority_levels.get(self.job_config.data_type, '0')})
        self.triggering_input_names = [dep.to_dagster_asset().to_user_string() for dep in self.job_config.triggering_deps]
    
    def build_asset(self):
        """
        This builds the Asset for Dagster for a particular job. This function will:

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
            output_assets[out.to_dagster_asset().to_user_string()] = AssetOut(is_required=False)

        @multi_asset(
            name=f"{self.job_config.to_dagster_asset().to_user_string()}_multi_asset_op",
            deps=input_keys, 
            partitions_def=self.partitions_def,
            outs=output_assets
        )
        def _generic_batch_submitter(context: AssetExecutionContext):
            parts = context.partition_key.split("_")
            start_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).date()
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
                raise Failure(description="Processing failed: Dependency inputs were missing.")
            with db.Session() as session:
                job_version = self._determine_job_version(
                                                session=session,
                                                instrument=self.job_config.source,
                                                descriptor=self.job_config.descriptor,
                                                start_date=start_date,
                                                data_level=self.job_config.data_type,
                                                current_dependencies=dependency_inputs.serialize(),
                                            )
                context.log.info(f"Job Version to Use: {job_version}")
            
                job_node = {"data_source":self.job_config.source,
                            "data_type":self.job_config.data_type,
                            "descriptor":self.job_config.descriptor}
                submit_response = self.try_to_submit_job(
                                                        session,
                                                        job_node,
                                                        start_date.strftime("%Y%m%d"),
                                                        job_version,
                                                        dependency_inputs.serialize(),
                                                        repoint=pointing_number,
                                                    )
                context.log.info(f"Submit response: {submit_response.status} - {submit_response.message}, {submit_response.job}")
                
                output_files = []
                if submit_response.status == 'submitted':
                    for output in self.job_config.outputs:
                        context.log.info(f"Waiting for data product {output.source}_{output.data_type}_{output.descriptor} to complete")
                        file = self.wait_for_file(context,
                                                session,
                                                output,
                                                job_version,
                                                submit_response.job,
                                                repointing=pointing_number,
                                                start_date=start_date.strftime("%Y%m%d"),
                                                inputs = dependency_inputs.serialize())

                        if file:
                            output_files.append(file)
                    if not output_files:
                        raise Failure(description="Processing failed, no files ")
                    else:
                        for f in output_files:
                            yield f
                else:
                    return SkipReason(f"Batch Job Status: {submit_response.status} - {submit_response.message}, {submit_response.job}")

        # Return the generated function back to Dagster
        return _generic_batch_submitter

    def wait_for_file(self,
                      context,
                      session: db.Session,
                      output: DependencyNode,
                      job_version: str,
                      job_info: dict,
                      start_date: str = None,
                      repointing: int = None,
                      inputs = {}):
        
        if start_date is None and repointing is None:
            raise ValueError("You must at least provide either start_date or repointing")
        
        parsed_start_date = None
        if start_date is not None:
            parsed_start_date = datetime.datetime.strptime(start_date, "%Y%m%d")

        timeout = 1200
        timeout_start = time.time()
        while time.time() < timeout_start + timeout:
            job_completed = (
                session.query(models.ProcessingJob)
                .filter(models.ProcessingJob.instrument == job_info['instrument'],
                    models.ProcessingJob.data_level == job_info['data_level'],
                    models.ProcessingJob.descriptor == job_info['descriptor'],
                    models.ProcessingJob.start_date == job_info['start_date'],
                    models.ProcessingJob.repointing == job_info['repointing'],
                    models.ProcessingJob.dependency_hash == job_info['dependency_hash'],
                    models.ProcessingJob.version == job_info['version'],
                    models.ProcessingJob.status.in_(
                            [models.Status.FAILED.value, models.Status.SUCCEEDED.value]
                        ))
                .order_by(models.ProcessingJob.version.desc())
                .first()
            )
            if not job_completed:
                time.sleep(60)
            else:
                filters = [
                    models.ScienceFiles.instrument == output.source,
                    models.ScienceFiles.data_level == output.data_type,
                    models.ScienceFiles.descriptor == output.descriptor,
                    models.ScienceFiles.version == job_version
                ]
                if repointing is not None:
                    filters.append(models.ScienceFiles.repointing == int(repointing))
                if parsed_start_date is not None:
                    filters.append(models.ScienceFiles.start_date == parsed_start_date)
                created_file = session.query(models.ScienceFiles).filter(*filters).first()
                if created_file:
                    context.log.info(f"Found file {os.path.basename(created_file.file_path)}! Creating Asset.")
                    materialization = get_materialization_result(context,
                                                                output.to_dagster_asset(),
                                                                context.partition_key,
                                                                [os.path.basename(created_file.file_path)],
                                                                str(int(job_version[1:])),
                                                                "science",
                                                                inputs = inputs)
                    if materialization:
                        return materialization
                break

    def _parse_dates_from_key(self, 
                              partition_key: str) -> tuple[datetime.datetime, datetime.datetime]:
        """
        Extracts start and end datetimes from a string formatted like:
        '{name}_%Y-%m-%dT%H:%M:%S_to_%Y-%m-%dT%H:%M:%S'
        """
        if not partition_key:
            return None, None
            
        date_range = partition_key.split('_', 1)[1]
        if "_to_" in date_range:
            p_start_str, p_end_str = date_range.split("_to_")
            p_start = datetime.datetime.strptime(p_start_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            
        return p_start, p_end

    def _get_overlapping_target_partitions(self, upstream_partition_key, up_start, up_end, instance):
        """
        Evaluates the upstream date range against existing downstream partitions.
        Returns a list of downstream partition keys that overlap.
        """
        target_keys = []
        # Fetch the currently known dynamic partitions for your target asset
        existing_downstream_keys = instance.get_dynamic_partitions(self.partitions_def.name)

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
        """
        Factory function that returns a Dagster sensor monitoring the dependencies
        and triggering this asset when overlapping data arrives.

        1) Checks for all of the latest asset materializations in dagster for all dependencies of this product
        2) Loops through the latest materializations
        3) Determines what time range those materializations belong to
        4) Determines what partition keys of this asset those new materializations belong to
        5) For each affected partition, we determine the dependencies. If we have all dependencies, we are good! 
        6) Yield a RunRequest for a job to make the asset for the partitions that have all dependencies.
        """
        sensor_name = f"{self.job_config.to_dagster_asset().to_user_string()}_kickoff_sensor"
        @sensor(
            name=sensor_name,
            job=self.dagster_job,
            minimum_interval_seconds=self.sensor_schedule,
        )
        def _sensor(context: SensorEvaluationContext):
            
            # Load the cursor to track which events we have already seen.
            # The cursor maps `{asset_name: last_processed_storage_id}`.
            cursors = json.loads(context.cursor) if context.cursor else {}
            new_cursors = cursors.copy()
            sensor_start_time = time.time()
            # Iterate through each dependency to find unconsumed materializations
            for dependency in self.job_config.inputs:

                dep_name = dependency.to_dagster_asset().to_user_string()
                context.log.info(f"Checking new dependencies for: {dep_name}")

                # Fetch the evaluated event ID for this specific dependency
                last_event_id = cursors.get(dep_name, 0)
                filter = EventRecordsFilter(
                        event_type=DagsterEventType.ASSET_MATERIALIZATION,
                        asset_key=dependency.to_dagster_asset(),
                        after_cursor=last_event_id,
                    )
                new_events = context.instance.get_event_records(filter, limit=100, ascending=True)
                for record in new_events:
                    # Update our new cursor marker to the highest storage ID seen
                    new_cursors[dep_name] = record.storage_id
                        
                    upstream_partition_key = record.event_log_entry.dagster_event.partition
                    up_start, up_end = self._parse_dates_from_key(upstream_partition_key)
                    context.log.info(f"Found one new dependency at {record.event_log_entry.dagster_event.partition}!")

                    if not up_start or not up_end:
                        continue
                    
                    # Kick off jobs in a range around the file
                    # TODO: This is probably too broad. But for now, this 
                    # is probably fine unless we start getting timeouts. 
                    if dependency.dependency_query_time_range:
                        time_range = int(dependency.dependency_query_time_range[0][0])
                        up_start = up_start + datetime.timedelta(days=-time_range)
                        up_end = up_end + datetime.timedelta(days=time_range)
                    
                    # Calculate overlap
                    target_partitions = self._get_overlapping_target_partitions(
                        upstream_partition_key, up_start, up_end, context.instance
                    )
                    
                    for target_partition in target_partitions:
                        # Check if this partition has already been materializied
                        runs = context.instance.get_runs(
                                                filters=RunsFilter(
                                                    job_name=self.dagster_job_name,
                                                    tags={"dagster/partition": target_partition}
                                                ),
                                                limit=1  # Limit to 1 since we only care about existence
                                            )
                        if (runs and (dep_name in self.triggering_input_names)) or not runs:
                            # Queue the RunRequest
                            target_start, target_end = self._parse_dates_from_key(target_partition)
                            dependencies = self.get_dependencies(context, target_start, target_end)
                            if dependencies:
                                tags={"dependencies": dependencies.serialize()}
                                run_key = f"{self.job_config.to_dagster_asset().to_user_string()}_{target_partition}_{self._dependency_hash(dependencies.serialize())}".replace(":", "")
                                context.log.info(f"Yielding a run request with ID: {run_key} on partition {target_partition}.")
                                yield RunRequest(
                                                partition_key=target_partition,
                                                run_key=f"{self.job_config.to_dagster_asset().to_user_string()}_{target_partition}_{self._dependency_hash(dependencies.serialize())}",
                                                tags=tags
                                            )
                        elif runs and (dep_name not in self.triggering_input_names):
                            context.log.info("We have already materialized something like this, and this dependency does not trigger new processing.")
                    
                    if (time.time() - sensor_start_time) > 30:
                        context.log.info("Sensor took too long, will inspect new items on the next run. ")
                        break # To keep the sensor short, we'll force it to stop analysis after 30 seconds. It will pick up again.  
                if (time.time() - sensor_start_time) > 30:
                        context.log.info("Sensor took too long, will inspect new items on the next run. ")
                        break # To keep the sensor short, we'll force it to stop analysis after 30 seconds. It will pick up again.  
                
            # Lock in the new cursor state
            context.update_cursor(json.dumps(new_cursors))

        return _sensor

    def get_dependencies(self, 
                         context: AssetExecutionContext, 
                         target_start: datetime.datetime, 
                         target_end: datetime.datetime):
        """Get the dependencies for the job using the DependencyResolver."""
        # Iterate through each upstream dependency
        processing_inputs = processing_input.ProcessingInputCollection()
        context.log.info(f"Checking for all dependencies existing between {target_start} and {target_end}")

        for input in self.job_config.inputs:
            dep_name = input.to_dagster_asset().to_user_string()
            found_dep=False
            context.log.info(f"Checking out {dep_name}")
            metadata_list = input.get_all_files_in_time_range(context,
                                                              target_start,
                                                              target_end)
            dependency_inputs = defaultdict(list)
            for metadata in metadata_list:
                if "file_names" in metadata:
                    found_dep = True # We can finally say we have found at least one dependency
                    # Dagster wraps metadata in a MetadataValue object, so we call .value
                    file_names = metadata["file_names"].value
                    # Handle both single strings and lists of files safely
                    if isinstance(file_names, str):
                        file_names = [file_names]
                    if file_names:
                        context.log.info(f"The file names of the matching partition: {file_names}")
                    input_type = metadata["input_type"].value
                    dependency_inputs[input_type].extend(file_names)

            # After all the files are found for this dependency add them to the
            # processing input collection as a single Input
            if len(dependency_inputs.keys()) > 1:
                raise ValueError(f"Multiple data types for the same DependencyNode is not supported. Found these types for {dep_name}: {list(dependency_inputs.keys())}")
            if len(dependency_inputs.keys()) == 1:
                input_type = next(iter(dependency_inputs))
                files = dependency_inputs[input_type]
                if input_type == "science":
                    processing_inputs.add(processing_input.ScienceInput(*files))
                if input_type == "ancillary":
                    processing_inputs.add(processing_input.AncillaryInput(*files))
                if input_type == "spin":
                    processing_inputs.add(processing_input.SpinInput(*files))
                if input_type == "repoint":
                    processing_inputs.add(processing_input.RepointInput(files[0]))
                if input_type == "spice":
                    processing_inputs.add(processing_input.SPICEInput(*files))


            if not found_dep and input.required:
                # If we found nothing and this is required, don't return anything.
                context.log.info(f"Not enough information to process. Missing {dep_name} in range {str(target_start)} to {str(target_end)}")
                return

        return processing_inputs

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
            for file in dep['files']:
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

        The image digest is a sha256 hash of the image manifest and is a unique identifier
        for the specific version of the container image used in the batch job.
        This is important for tracking which version of the code is being used for each
        job.

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
        # Parse the container image URI to get the registry id, repository name and image
        # tag and use those to call describe_images and get the image digest.
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

    def try_to_submit_job(
            self,
            session: db.Session,
            job_info: dict,
            start_date: str,
            version: str,
            serialized_dependencies: str,
            repoint: int | None = None,
        ):
        """Try to submit a batch job with the given job information.

        Parameters
        ----------
        session : orm session
            Database session.
        job_info : dict
            Dictionary containing components with dates and versions appended.
        start_date : str
            Start date of the data in the format 'YYYYMMDD'.
        version : str
            Version of the job.
        serialized_dependencies : str
            The serialized ProcessingInputCollection of the upstream
            dependencies.
        repoint : int, optional
            The repointing number for the job, if applicable. Default is None. Should
            be just an integer, no "repoint" prefix.
        """
        instrument = job_info["data_source"]
        data_level = job_info["data_type"]
        descriptor = job_info["descriptor"]

        # Serialize the upstream dependencies and write them to a JSON file. The Imap
        # processing code will read the JSON file and deserialize the dependencies. This is
        # to avoid passing a large string through the batch job command line.

        # Calculate the dependency hash, if dependencies
        # change, the hash changes. Combined with the unique constraint on
        # (dependency_hash, container_image_digest), this gives us duplicate detection:
        # same deps + same digest = IntegrityError = job skipped
        # For a given instrument, data_level, start_date ect. If either the deps change or
        # the image changes then a new job is allowed with a bumped version number.
        dep_hash = self._dependency_hash(serialized_dependencies)
        dep_descriptor = f"{descriptor}-{dep_hash}"
        dependency_file = DependencyFilePath.generate_from_inputs(
            instrument=instrument,
            data_level=data_level,
            descriptor=dep_descriptor,
            start_time=start_date,
            version=version,
            extension="json",
            repointing=repoint,  # since we can have different repointings on the same day
        )
        dependency_file_path = dependency_file.construct_path()
        response = self.upload_dependency_file(dependency_file_path, serialized_dependencies)
        # If response is None, then the upload failed and we should skip submitting the job.
        if not response:
            return BatchJobSubmit(
                status="failed",
                message="Dependency JSON file upload failed."
            )

        batch_command = [
            "--instrument",
            instrument,
            "--data-level",
            data_level,
            "--descriptor",
            descriptor,
            "--start-date",
            start_date,
            "--version",
            version,
            "--dependency",
            dependency_file_path.name,
            "--upload-to-sdc",
        ]

        if repoint is not None:
            batch_command.extend(["--repointing", f"repoint{repoint:05d}"])
        # Get the necessary AWS information
        # NOTE: These are here for easier mocking in tests rather than at the module level
        step = "-l3" if data_level >= "l3" else ""
        job_definition = f"ProcessingJob-{instrument}{step}"

        # Capture the container image and digest right before submitting the job.
        # This ensures the image digest that will be used is recorded. We record this
        # information here and not in indexer.py to avoid race conditions where the image
        # could change during job execution.
        container_image_digest = self._get_container_image_digest()

        # All of our upstream requirements have been met.
        # Try to insert a record into the Processing Jobs table
        # If this job already exists, then we will get an integrity error
        # and know that some other process has already taken care of it
        processing_job = models.ProcessingJob(
            status=models.Status.INPROGRESS,
            instrument=instrument,
            data_level=data_level,
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            version=version,
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

        logger.info(
            f"Wrote job INPROGRESS to Processing Jobs Table with id: {processing_job.id}"
        )
        # NOTE: The batch job name should contain only alphanumeric characters and hyphens
        # E.g. "codice-l1a-sci-job-1"
        # The `processing_job.id` is used later for updating the job processing table
        job_name = f"{instrument}-{data_level}-{descriptor}-job-{processing_job.id}"
        job_queue = "ProcessingJobQueue"

        BATCH_CLIENT.submit_job(
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
            )

    def upload_dependency_file(self,
                               dependency_file_path: Path, 
                               serialized_dependencies: str):
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
                    "authorizer": {"lambda": {"scope": "write", "apiKey": "batch-starter"}}
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