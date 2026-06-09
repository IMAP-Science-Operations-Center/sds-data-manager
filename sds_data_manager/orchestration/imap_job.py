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
from dagster import (
    AssetExecutionContext,
    Failure,
    SensorEvaluationContext,
    sensor,
    EventRecordsFilter,
    DagsterEventType,
    RunRequest,
    multi_asset,
    AssetOut,
    define_asset_job,
    RunsFilter,
    SkipReason,
    DagsterRunStatus,
    RetryRequested
)
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.types import DependencyNode, ProcessingJobNode
from sds_data_manager.orchestration import custom_partitions, config, spin, repoint_file, spice, dagster_utilities
from sds_data_manager.orchestration.dagster_utilities import get_materialization_result
from sds_data_manager.lambda_code.SDSCode.api_lambdas import upload_api
from imap_data_access import processing_input, DependencyFilePath, VALID_DATALEVELS
import boto3
from botocore.exceptions import ClientError
from sqlalchemy import func, and_, or_, desc
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

class MissingDependencies(Exception):
    # This will raise from the get_dependencies function if crucial data is not found
    pass

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
        self.sensor_run_frequency = config.sensor_schedules.get(self.job_config.data_type, 600)
        
        outputs_for_job = [x.to_dagster_asset() for x in self.job_config.outputs]
        self.dagster_job_name = f"{self.job_config.to_dagster_asset().to_user_string()}_processing_job"
        self.dagster_job = define_asset_job(name=self.dagster_job_name,
                                           selection=outputs_for_job,
                                           tags={"dagster/priority": priority_levels.get(self.job_config.data_type, '0')})
        self.triggering_input_names = [dep.to_dagster_asset().to_user_string() for dep in self.job_config.triggering_deps]
    
    def build_asset(self):
        """
        This builds the Asset for Dagster for a particular job. 
        
        This function will:

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
            
            # TODO: Is this needed if we only check every few minutes? 
            # Before doing anything, check if any of the dependencies are currently running or about to run. 
            # If so, let us try again in 5 minutes. 
            # dependencies_running = self._check_for_running_dependencies()
            # if dependencies_running:
            #    raise RetryRequested(max_retries=10, seconds_to_wait=600)
            
            # Figure out what time window this specific run is responsible for
            target_partition = context.partition_key
            target_start, target_end = dagster_utilities.parse_dates_from_partition_key(target_partition)

            # Get the repoint number of the job, based on the partition name.  
            parts = context.partition_key.split("_")
            if "repoint" in parts[0]:
                target_pointing_number = int(parts[0][7:])
            else:
                target_pointing_number = None

            # Get Dependencies 
            with db.Session() as session:
                try:
                    dependency_inputs = self.get_dependencies(session,
                                                             context, 
                                                             target_start, 
                                                             target_end)

                    if not dependency_inputs:
                        return SkipReason("Dependency inputs were missing.")
                    
                except MissingDependencies as e:
                    return SkipReason(str(e))
                
                context.log.info(f"Using the following dependencies: {dependency_inputs.serialize()}")

                # We have the dependencies, lets try to submit the job!
                job_version = self._determine_job_version(session=session,
                                                          start_date=target_start,
                                                          current_dependencies=dependency_inputs.serialize(),
                                                          repointing=target_pointing_number
                                                         )
                context.log.info(f"Job Version to Use: {job_version}")
                
                
                submit_response = self.try_to_submit_job(session,
                                                        target_start,
                                                        job_version,
                                                        dependency_inputs.serialize(),
                                                        repoint=target_pointing_number,
                                                    )
                context.log.info(f"Submit response: {submit_response.status} - {submit_response.message}, {submit_response.job}")
                
                if submit_response.status == 'submitted':
                    batch_status = self.wait_for_batch_job(session, submit_response.job)
                    time.sleep(60) # Give the indexer time to pick up the files 
                    output_files = self.find_outputs(context,
                                                    session,
                                                    job_version=job_version,
                                                    start_date=target_start,
                                                    repointing=target_pointing_number,
                                                    inputs=dependency_inputs.serialize())

                    if not output_files:
                        if batch_status == models.Status.SUCCEEDED.value:
                            return SkipReason("No files were output, though the job succeeded.")
                        if batch_status == models.Status.FAILED.value:
                            raise Failure(description="Processing failed and no files were generated.")                      
                    else:
                        for f in output_files:
                            yield f
                        if batch_status == models.Status.FAILED.value:
                            raise Failure(description="Processing failed, though there are previous files we can use.")
                elif submit_response.status=='skipped':
                    # If we skipped the job, let us materialize any previous outputs so that dagster knows about them at least
                    output_files = self.find_outputs(context,
                                                     session,
                                                     start_date=target_start,
                                                     repointing=target_pointing_number,
                                                     inputs=dependency_inputs.serialize())
                    for f in output_files:
                        yield f

                    if not output_files:
                        return SkipReason(f"Batch Job Status: {submit_response.status} - {submit_response.message}, {submit_response.job}")
                else:
                    raise Failure(description=submit_response.message)
                
        # Return the generated function back to Dagster
        return _generic_batch_submitter

    def wait_for_batch_job(self,
                           session: db.Session,
                           job_info: dict):        

        timeout = 3600
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
                return job_completed.status.name
            
        # If we time out, return a failure
        return models.Status.FAILED.value


    def find_outputs(self,
                       context,
                       session: db.Session,
                       job_version: str = None,
                       start_date: datetime.datetime = None,
                       repointing: int = None,
                       inputs = {}):
        
        output_materializations = []

        if start_date is None and repointing is None:
            raise ValueError("You must at least provide either start_date or repointing")
        
        for output in self.job_config.outputs:
            filters = [
                models.ScienceFiles.instrument == output.source,
                models.ScienceFiles.data_level == output.data_type,
                models.ScienceFiles.descriptor == output.descriptor,
            ]
            if job_version:
                filters.append(models.ScienceFiles.version == job_version)
            if repointing is not None:
                filters.append(models.ScienceFiles.repointing == int(repointing))
            if start_date is not None:
                filters.append(models.ScienceFiles.start_date == start_date.date())
            created_file = session.query(models.ScienceFiles).filter(*filters).distinct(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                ).order_by(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                    models.ScienceFiles.version.desc()
                ).first()
            if created_file:
                context.log.info(f"Found file {os.path.basename(created_file.file_path)}! Creating Asset.")
                materialization = get_materialization_result(context,
                                                            output.to_dagster_asset(),
                                                            context.partition_key,
                                                            [os.path.basename(created_file.file_path)],
                                                            str(int(created_file.version[1:])),
                                                            "science",
                                                            inputs = inputs)
                if materialization:
                    output_materializations.append(materialization)
        return output_materializations

    def _check_for_running_dependencies(self, context):
        '''This function checks if anything upstream of this file is currently running.
        '''
        input_set = set([dep.to_dagster_asset() for dep in self.job_config.inputs])
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
            if run.asset_selection and input_set.intersection(run.assest_selection):
                conflict_found = True
                context.log.info(f"Dependency found in active run: {run.run_id}")
                break
        return conflict_found

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
            down_start, down_end = dagster_utilities.parse_dates_from_partition_key(down_key)
            if not down_start or not down_end:
                continue

            # Math logic for overlapping date intervals
            if up_start <= down_end and up_end >= down_start:
                target_keys.append(down_key)

        return target_keys
    
    def build_sensor(self):
        """
        Factory function that returns a Dagster sensor monitoring the dependencies
        and triggering an asset if there are new ones. 

        Note that this does not perform all dependency checks. That job is part of the @asset's job. 
        This job simply alerts the asset if there is the *potential* to start up a new job. 

        1) Checks for all of the latest asset materializations in dagster for all dependencies of this product
        2) Loops through the latest materializations
        3) Determines what time range those materializations belong to
        4) Determines what partition keys of this asset those new materializations belong to
        5) For each affected partition, we determine if we really should trigger or not
        6) Yield a RunRequest for a job to make the asset for the partitions that have all dependencies.
        """
        sensor_name = f"{self.job_config.to_dagster_asset().to_user_string()}_kickoff_sensor"
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
            
            # Iterate through each dependency to find the materializations after the most recent run
            for dependency in self.job_config.inputs:
                target_partitions = [] # Partitions to kick off
                dep_name = dependency.to_dagster_asset().to_user_string()
                context.log.info(f"Checking new dependencies for: {dep_name}")
                if dependency.data_type in VALID_DATALEVELS: 
                    # New science inputs
                    target_partitions = self.trigger_from_new_science_inputs(context, 
                                                                             dependency, 
                                                                             new_cursors, 
                                                                             sensor_start_time)
                if dependency.data_type=='spice':
                    # New SPICE inputs
                    target_partitions = self.trigger_from_new_non_science_inputs(context,
                                                                                 dependency,
                                                                                 new_cursors,
                                                                                 models.SPICEFiles,
                                                                                 models.SPICEFiles.kernel_type,
                                                                                 None,
                                                                                 "min_date_datetime",
                                                                                 "max_date_datetime")
                elif dependency.data_type=='spin':
                    # New spin inputs
                    target_partitions = self.trigger_from_new_non_science_inputs(context,
                                                                                 dependency,
                                                                                 new_cursors,
                                                                                 models.SpinFiles)
                elif dependency.data_type=='ancillary':
                    # New ancillary inputs
                    target_partitions = self.trigger_from_new_non_science_inputs(context,
                                                                                 dependency,
                                                                                 new_cursors,
                                                                                 models.AncillaryFiles,
                                                                                 models.AncillaryFiles.instrument,
                                                                                 models.AncillaryFiles.descriptor)
                # Now we loop through each partition that we received new data for, and determine
                # if we need to start it again.
                for target_partition in target_partitions:
                    # Check if this partition has already been run successfully 
                    runs = context.instance.get_runs(
                                            filters=RunsFilter(
                                                job_name=self.dagster_job_name,
                                                statuses=[DagsterRunStatus.SUCCESS],
                                                tags={"dagster/partition": target_partition}
                                            ),
                                            limit=1  # Limit to 1 since we only care about existence
                                        )
                    
                    # If this has never been run, OR it has run, but we always trigger from this dependency 
                    if (runs and (dep_name in self.triggering_input_names)) or not runs:
                        run_key = f"{self.job_config.to_dagster_asset().to_user_string()}_{target_partition}_{job_suffix}"
                        context.log.info(f"Yielding a run request with ID: {run_key} on partition {target_partition}.")

                        # Go to _generic_batch_sumbitter 
                        yield RunRequest(partition_key=target_partition,run_key=run_key) 

                    elif runs and (dep_name not in self.triggering_input_names):
                        context.log.info("We have already materialized something like this, and this dependency does not trigger new processing.")
                    
                if (time.time() - sensor_start_time) > 30:
                    context.log.info("Sensor took too long, will inspect new items on the next run. ")
                    break # To keep the sensor short, we'll force it to stop analysis after 30 seconds. It will pick up again.  
                
            # Lock in the new cursor state
            context.update_cursor(json.dumps(new_cursors))

        return _sensor

    def trigger_from_new_non_science_inputs(self,
                                            context,
                                            dependency,
                                            cursors,
                                            table,
                                            source_column=None,
                                            descriptor_column=None,
                                            datetime_start_column="start_date",
                                            datetime_end_column="end_date"):
        '''
        This function parses the main database tables for anything new. 
        '''
        if dependency.source in ['leapseconds', 'spacecraft_clock', 'imap_frames', 'science_frames', 'planetary_constants']:
            # Never trigger from these
            return []
        
        partitions_to_run = []
        dep_name = dependency.to_dagster_asset().to_user_string()
        cursor_str = cursors.get(dep_name, config.MISSION_START_TIME)
        latest_ingestion_date = datetime.datetime.fromisoformat(cursor_str).replace(tzinfo=datetime.timezone.utc)

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
            
            if cursor_str == config.MISSION_START_TIME:
                # Just process everything, don't bother looping through the entire database
                partitions = dagster_utilities.get_affected_partitions(context, 
                                                                       self.partitions_def, 
                                                                       datetime.datetime.fromisoformat(config.MISSION_START_TIME), 
                                                                       datetime.datetime.fromisoformat(config.MISSION_END_TIME))
                partitions_to_run.extend(partitions)
            else:
                for file in new_files:
                    min_dt = getattr(file, datetime_start_column)
                    max_dt = getattr(file, datetime_end_column)
                    if not min_dt or not max_dt:
                        continue
                    partitions = dagster_utilities.get_affected_partitions(context, self.partitions_def, min_dt, max_dt)

                    partitions_to_run.extend(partitions)
            cursors[dep_name] = latest_ingestion_date.isoformat()

        return partitions_to_run

    def trigger_from_new_science_inputs(self, 
                                        context: AssetExecutionContext, 
                                        dependency: DependencyNode, 
                                        cursors: dict,
                                        sensor_start_time: float):
        '''
        This function will return a list of partitions to trigger from 
        new science files detected. 
        '''
        dep_name = dependency.to_dagster_asset().to_user_string()
        context.log.info(f"Checking new dependencies for: {dep_name}")

        # Fetch the 10 new science materializations from dagster
        last_event_id = cursors.get(dep_name, 0)
        filter = EventRecordsFilter(
                event_type=DagsterEventType.ASSET_MATERIALIZATION,
                asset_key=dependency.to_dagster_asset(),
                after_cursor=last_event_id,
            )
        new_events = context.instance.get_event_records(filter, limit=100, ascending=True)
        

        partitions_to_run = []
        for record in new_events:
            # Update our new cursor marker to the highest storage ID seen
            if last_event_id < record.storage_id:
                last_event_id = record.storage_id

            # Get the dates of this new science file
            upstream_partition_key = record.event_log_entry.dagster_event.partition
            up_start, up_end = dagster_utilities.parse_dates_from_partition_key(upstream_partition_key)
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
            
            # Calculate overlap between the dependency file and this file
            target_partitions = self._get_overlapping_target_partitions(
                upstream_partition_key, up_start, up_end, context.instance
            )
            partitions_to_run.extend(target_partitions)                    
            
            # To keep the sensor short, we'll force it to stop analysis after 30 seconds. It will pick up again. 
            if (time.time() - sensor_start_time) > 30:
                context.log.info("Sensor took too long, will inspect new items on the next run. ")
                break 
        
        cursors[dep_name] = last_event_id

        return partitions_to_run


    def get_ancillary_files_inputs(self,
                            session,
                            target_start: datetime.datetime,
                            target_end: datetime.datetime) -> list[str]:
        # Query for ancillary files where the start_date is less than or equal to
        # the input end_date, and the end_date is either greater than or equal to the
        # input start_date or is None. For example, if the input start_date is
        # '20240524' and the end_date is '20240527', the query could return an ancillary
        # file with the date range ('20240525', '20240528').
        anc_processing_inputs = []
        for input in self.job_config.inputs:
            if input.data_type=='ancillary':
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
                session.query(table.start_date, func.max(table.version).label("latest_version"))
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
                    raise MissingDependencies(f"Missing dependency for {input.to_dagster_asset().to_user_string()} between {target_start} and {target_end}")
                filenames = [os.path.basename(record.file_path) for record in records]
                anc_processing_inputs.append(processing_input.AncillaryInput(*filenames))
        return anc_processing_inputs
    
    def get_spin_files_inputs(self,
                        session,
                        target_start: datetime.datetime,
                        target_end: datetime.datetime) -> list[str]:
        
        spin_files = spin.get_upstream_dependency_inputs_spin(target_start.replace(hour=0, minute=0, second=0),
                                                              target_end,
                                                              False,
                                                              session)
        if not spin_files and self.job_config.spin_input.required:
            raise MissingDependencies(f"Missing dependency for {self.job_config.spin_input.to_dagster_asset().to_user_string()} between {target_start} and {target_end}")
        return spin_files

    def get_repoint_file_inputs(self,
                                session,
                                target_start,
                                target_end):
        file = repoint_file.get_upstream_dependency_inputs_repoint(target_start, 
                                                                    target_end,
                                                                    session)
        if not file and self.job_config.repoint_input.required:
            raise MissingDependencies(f"Missing dependency for {self.job_config.repoint_input.to_dagster_asset().to_user_string()} between {target_start} and {target_end}")
        
        return file
    
    def get_spice_file_inputs(self,
                              session,
                              target_start,
                              target_end):
        
        spice_files = spice.get_upstream_dependency_inputs_spice(self.job_config.spice_types, 
                                                                target_start,
                                                                target_end)
        return spice_files
    
    def get_science_files_inputs(self,
                                context,
                                target_start,
                                target_end):
        science_processing_inputs = []
        for input in self.job_config.science_inputs:
            dep_name = input.to_dagster_asset().to_user_string()
            found_dep=False
            metadata_list = input.get_all_files_in_time_range(context,
                                                              target_start,
                                                              target_end)
            science_files = []
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
                    science_files.extend(file_names)
            
            if not found_dep and input.required:
                # If we found nothing and this is required, don't return anything.
                raise MissingDependencies(f"Not enough information to process. Missing {dep_name} in range {str(target_start)} to {str(target_end)}")
            if science_files:
                science_processing_inputs.append(processing_input.ScienceInput(*list(set(science_files))))
        
        if not science_processing_inputs:
            # Return right away if we have zero science files.
            raise MissingDependencies(f"No science files were discovered. All jobs require at least one science file.")

        return science_processing_inputs

    def get_dependencies(self, 
                        session,
                        context: AssetExecutionContext, 
                        target_start: datetime.datetime, 
                        target_end: datetime.datetime):

        """Get the dependencies for the job using the DependencyResolver."""
        # Iterate through each upstream dependency
        processing_inputs = processing_input.ProcessingInputCollection()
        context.log.info(f"Checking for all dependencies existing between {target_start} and {target_end}")

        # Get Science files
        science_files = self.get_science_files_inputs(context, target_start, target_end)
        for inputs in science_files:
            processing_inputs.add(inputs)

        # Get Ancillary files
        if self.job_config.ancillary_inputs:
            ancillary_files = self.get_ancillary_files_inputs(session, target_start, target_end)
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

    def _determine_job_version(
        self,
        session: db.Session,
        start_date: datetime,
        current_dependencies: str,
        repointing: int | None = None,
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
        repointing : int, optional
            Repointing number. Versions are tracked independently per repointing so
            that multiple repoints on the same day each start at v001.

        Returns
        -------
        str
            The highest version number.
        """

        def filter_conditions(table):
            # Filter conditions for the query
            conditions = [
                table.instrument == self.job_config.source,
                table.data_level == self.job_config.data_type,
                table.descriptor == self.job_config.descriptor,
                table.start_date == start_date.date(),
                table.repointing == repointing
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
        if "all" in self.job_config.descriptor:
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
        if self.job_config.source == "spacecraft" and self.job_config.descriptor == "pointing-attitude":
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
                if "imap_sclk" not in file and ".repoint" not in file:
                    # We'll get rid of the spacecraft_clock kernel and repoint file. 
                    # These are updated frequently, and make zero difference to processing.
                    non_sclk_deps.append(file)
        # Append the image_digest
        sorted_files = sorted(list(set(non_sclk_deps)))
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

    def try_to_submit_job(
            self,
            session: db.Session,
            start_date: datetime.datetime,
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
        
        # Serialize the upstream dependencies and write them to a JSON file. The Imap
        # processing code will read the JSON file and deserialize the dependencies. This is
        # to avoid passing a large string through the batch job command line.

        # Calculate the dependency hash, if dependencies
        # change, the hash changes. Combined with the unique constraint on
        # (dependency_hash, container_image_digest), this gives us duplicate detection:
        # same deps + same digest = IntegrityError = job skipped
        # For a given instrument, data_level, start_date ect. If either the deps change or
        # the image changes then a new job is allowed with a bumped version number.

        start_date_str = start_date.strftime("%Y%m%d")
        dep_hash = self._dependency_hash(serialized_dependencies)
        dep_descriptor = f"{self.job_config.descriptor}-{dep_hash}"
        dependency_file = DependencyFilePath.generate_from_inputs(
            instrument=self.job_config.source,
            data_level=self.job_config.data_type,
            descriptor=dep_descriptor,
            start_time=start_date_str,
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
            self.job_config.source,
            "--data-level",
            self.job_config.data_type,
            "--descriptor",
            self.job_config.descriptor,
            "--start-date",
            start_date_str,
            "--version",
            version,
            "--dependency",
            dependency_file_path.name,
            "--upload-to-sdc",
        ]

        if repoint is not None:
            batch_command.extend(["--repointing", f"repoint{repoint:05d}"])
        
        # We will check here if this job has already failed with these specific dependencies
        conditions = [
                models.ProcessingJob.instrument == self.job_config.source,
                models.ProcessingJob.data_level == self.job_config.data_type,
                models.ProcessingJob.descriptor == self.job_config.descriptor,
                models.ProcessingJob.dependency_hash == dep_hash,
                models.ProcessingJob.start_date == start_date.date()
                ]
        conditions.append(
                    models.ProcessingJob.status.in_(
                        [models.Status.FAILED.value]
                    )
                )
        if repoint is not None:
            conditions.append(models.ProcessingJob.repointing == repoint)

        already_failed_job = (
            session.query(models.ProcessingJob)
            .filter(*conditions)
            .order_by(models.ProcessingJob.version.desc())
            .first()
        )
        if already_failed_job:
            return BatchJobSubmit(
                status="failed",
                message="This exact job has been submitted previously, and has already failed. No need to run it again."
            )
        
        # Get the necessary AWS information
        # NOTE: These are here for easier mocking in tests rather than at the module level
        step = "-l3" if self.job_config.data_type >= "l3" else ""
        job_definition = f"ProcessingJob-{self.job_config.source}{step}"

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
            instrument=self.job_config.source,
            data_level=self.job_config.data_type,
            descriptor=self.job_config.descriptor,
            start_date=datetime.datetime.strptime(start_date_str, "%Y%m%d"),
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
        job_name = f"{self.job_config.source}-{self.job_config.data_type}-{self.job_config.descriptor}-job-{processing_job.id}"
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