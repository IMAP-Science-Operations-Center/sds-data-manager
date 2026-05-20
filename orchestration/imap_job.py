"""IMAP job handler for managing dependencies and job submission."""

import json
import datetime
import re
import time
import boto3
import os
import logging
import hashlib
from dagster import (
    asset,
    AssetExecutionContext,
    Failure,
    AssetSelection,
    AssetKey,
    SensorEvaluationContext,
    sensor,
    EventRecordsFilter,
    DagsterEventType,
    RunRequest,
    SkipReason,
    DefaultSensorStatus,
    multi_asset,
    AssetOut,
    define_asset_job
)
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import DependencyConfigReader
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.types import DependencyNode
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import batch_starter
from orchestration import spice, spin, pointing_attitude
from orchestration.dagster_utilities import get_materialization_result
import imap_data_access
from imap_data_access import processing_input
from imap_data_access.io import download
import boto3
from sqlalchemy import func


# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class IMAPJobHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, asset_name, partition):
        """Initialize handler with job node and process dependencies.

        Parameters
        ----------
        potential_job_node : UpstreamDependencyNode
            The job node to process.
        """
        self.needs_spin = False
        self.needs_pointing_attitude = False
        self.needs_spice = False

        
        self.source, self.data_type, self.descriptor = asset_name.split("_")
        

        self.asset_name = asset_name.replace("-", "")
        dependency_config = DependencyConfigReader()
        key = (self.source, self.data_type, self.descriptor)
        self.outputs = dependency_config.outputs(key)
        potential_deps_list = list(dependency_config.inputs(key))
        
        self.partitions_def = dependency_config.partition(key)

        spice_types = []
        deps_list = []
        triggering_deps = []
        for dep in potential_deps_list:
            if dep.source == 'pointing_attitude':
                self.needs_pointing_attitude = True
                deps_list.append(asset_name+'_pointing_attitude_deps')
            elif dep.data_type == 'spice':
                deps_list.append(asset_name+'_spice_deps')
                spice_types.append(dep.source)
                self.needs_spice = True
            elif dep.data_type == 'spin':
                deps_list.append(asset_name+'_spin_deps')
                self.needs_spin = True
            elif dep.data_type == 'ancillary':
                asset_name = dep.source + '_' + dep.data_type + '_' + dep.descriptor
                deps_list.append(asset_name)
            else:
                asset_name = dep.source + '_' + dep.data_type + '_' + dep.descriptor
                deps_list.append(asset_name)
                if dep.trigger_job:
                    triggering_deps.append(asset_name)

        self.spice_types = spice_types
        self.deps_list = deps_list
        self.triggering_deps = triggering_deps

        outputs_for_job = [x.descriptor.replace("-","") for x in self.outputs]
        self.job = define_asset_job(name=f"{self.asset_name}_processing_job",
                                    selection=AssetSelection.keys(*outputs_for_job)
                                    )
    
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
        deps_keys = [AssetKey(dep.replace("-", "")) for dep in self.deps_list]
        output_assets = {}
        for out in self.outputs:
            output_assets[out.descriptor.replace("-", "")] = AssetOut(is_required=False) 

        @multi_asset(
            name=f"{self.asset_name}_multi_asset_op",
            deps=deps_keys, 
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
            already_processed = False
            with db.Session() as session:
                for output in self.outputs:
                    descriptor = output.descriptor
                    output_asset_name = f"{output.source}_{output.data_type}_{output.descriptor}"
                    previous_file = self.is_duplicate_job(context,
                                                        session,
                                                        dependency_inputs,
                                                        self.source,
                                                        self.data_type,
                                                        descriptor,
                                                        self.descriptor,
                                                        start_date,
                                                        pointing_number)
            
                    if previous_file:
                        already_processed = True
                        materialization = get_materialization_result(context,
                                                                    output_asset_name,
                                                                    context.partition_key,
                                                                    [os.path.basename(previous_file.file_path)],
                                                                    previous_file.version,
                                                                    "science")
                        if materialization:
                            yield materialization

                if not already_processed:
                    job_version = self._determine_job_version(
                                                    session=session,
                                                    instrument=self.source,
                                                    descriptor=self.descriptor,
                                                    start_date=start_date.strftime("%Y%m%d"),
                                                    data_level=self.data_type,
                                                    current_dependencies=dependency_inputs.serialize(),
                                                )
                    context.log.info(f"Job Version to Use: {job_version}")
                
                    '''
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
                    '''
                    #if submit_status.status == 'submitted':
                    for output in self.outputs:
                        output_asset_name = f"{output.source}_{output.data_type}_{output.descriptor}"
                        context.log.info(f"Waiting for data product {output_asset_name} to complete.")
                        context.log.info(f"Start Date: {start_date}")
                        context.log.info(f"Job Version: {job_version}")
                        file = self.wait_for_file(context,
                                                session,
                                                output_asset_name,
                                                job_version,
                                                repointing=pointing_number,
                                                start_date=start_date,
                                                inputs = dependency_inputs.serialize())

                        if file:
                            yield file
                        # TODO: We should not yield right away. We should collect up anything that this asset has generated, 
                        # yield what we can, and determine if anything required in the output is missing, then yield a failure. 

        # Return the generated function back to Dagster
        return _generic_batch_submitter

    def build_spice_deps_asset(self):
        '''
        This function will take in various spice_types, and then populate 
        each repoint partition with those spice types. 
        '''

        @asset(
            name=self.asset_name+"_spice_deps",
            partitions_def=self.partitions_def,
            output_required=False
        )
        def _generic_spice_maker(context):

            # Will use this in the future to limit SPICE queries 
            current_partition = context.partition_key
            parts = context.partition_key.split("_")
            start_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            end_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)

            spice_files = spice.get_upstream_dependency_inputs_spice(self.spice_types, 
                                                                    start_date,
                                                                    end_date)

            if spice_files:
                materialization = get_materialization_result(context,
                                                             self.asset_name+"_spice_deps",
                                                             current_partition,
                                                             spice_files,
                                                             "0",
                                                             "spice")
                if materialization:
                    yield materialization
            else:
                raise Failure(description="Processing failed: No data found")

        return _generic_spice_maker

    def wait_for_file(self,
                      context,
                      session,
                      asset_name,
                      job_version,
                      start_date=None,
                      repointing=None,
                      inputs = {}):
        if start_date is None and repointing is None:
            raise ValueError("You must at least provide either start_date or repointing")
        
        parsed_start_date = None
        if start_date is not None:
            parsed_start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        
        timeout = 3 # TODO: Set this for WAY higher once we're actually waiting for files. 
        timeout_start = time.time()
        while time.time() < timeout_start + timeout:
            filters = [
                models.ScienceFiles.instrument == asset_name.split("_")[0],
                models.ScienceFiles.data_level == asset_name.split("_")[1],
                models.ScienceFiles.descriptor == asset_name.split("_")[2],
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
                                                            asset_name.replace("-", ""),
                                                            context.partition_key,
                                                            [os.path.basename(created_file.file_path)],
                                                            str(int(job_version[1:])),
                                                            "science",
                                                            inputs = inputs)
                if materialization:
                    return materialization
            time.sleep(1) # TODO: Set this for WAY higher once we're actually waiting for files. 

    def build_spin_deps_asset(self):
        @asset(
            name=self.asset_name+"_spin_deps",
            partitions_def=self.partitions_def,
            output_required=False
        )
        def _generic_spin_maker(context):

            # Get time range from partition
            current_partition = context.partition_key
            parts = context.partition_key.split("_")
            start_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            end_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)

            spin_files = spin.get_upstream_dependency_inputs_spin(start_date,
                                                                  end_date)

            
            if spin_files:
                materialization = get_materialization_result(context,
                                                            self.asset_name+"_spin_deps",
                                                            current_partition,
                                                            spin_files,
                                                            "0",
                                                            "spin")
                if materialization:
                    yield materialization
            else:
                raise Failure(description="Processing failed: No data found")
            
        return _generic_spin_maker

    def build_attitude_pointing_deps_asset(self):
        @asset(
                name=self.asset_name+"_pointing_attitide_deps",
                partitions_def=self.partitions_def,
                output_required=False
        )
        def _generic_pointing_attitude_maker(context):

            current_partition = context.partition_key
            parts = context.partition_key.split("_")
            start_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            end_date = datetime.datetime.strptime(parts[-3], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)

            pointing_attitude_files = pointing_attitude.get_upstream_dependency_inputs_repoint(start_date,
                                                                                               end_date)

            
            if pointing_attitude_files:
                materialization = get_materialization_result(context,
                                                            self.asset_name+"_pointing_attitide_deps",
                                                            current_partition,
                                                            pointing_attitude_files,
                                                            "0",
                                                            "repoint")
                if materialization:
                    yield materialization
            else:
                raise Failure(description="Processing failed: No data found")
            
        return _generic_pointing_attitude_maker

    def _parse_dates_from_key(self, partition_key: str):
        """
        Extracts start and end datetimes from a string formatted like:
        '{name}_YYYYMMDD_to_YYYYMMDD'
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
        deps_keys = [AssetKey(dep) for dep in self.deps_list]
        sensor_name = f"{self.asset_name}_kickoff_sensor"
        @sensor(
            name=sensor_name,
            job=self.job,
            minimum_interval_seconds=60,
            default_status=DefaultSensorStatus.RUNNING
        )
        def _sensor(context: SensorEvaluationContext):
            
            # 1. Load the cursor to track which events we have already seen.
            # The cursor maps `{asset_name: last_processed_storage_id}`.
            cursors = json.loads(context.cursor) if context.cursor else {}
            new_cursors = cursors.copy()
            
            # 2. Iterate through each dependency to find unconsumed materializations
            for dep_key in deps_keys:
                
                dep_name = dep_key.to_user_string()
                context.log.info(f"Checking new dependencies for: {dep_name}")
                # Fetch the last evaluated event ID for this specific dependency
                last_event_id = cursors.get(dep_name)
                
                # Query the Dagster instance for strictly new materializations
                # TODO: Is dagster events how I want to query this stuff? Or should I use the main database? 
                filter = EventRecordsFilter(
                        event_type=DagsterEventType.ASSET_MATERIALIZATION,
                        asset_key=dep_key,
                        after_cursor=last_event_id,
                    )
                new_events = context.instance.get_event_records(filter, limit=100)
                
                for record in new_events:
                    # Update our new cursor marker to the highest storage ID seen
                    if not new_cursors.get(dep_name) or record.storage_id > new_cursors[dep_name]:
                        new_cursors[dep_name] = record.storage_id
                        
                    upstream_partition_key = record.event_log_entry.dagster_event.partition
                    up_start, up_end = self._parse_dates_from_key(upstream_partition_key)
                    
                    if not up_start or not up_end:
                        continue
                        
                    # 3. Calculate overlap
                    # TODO: The problem with this function is that it will take longer as we get more partitions.
                    # Is that ok? Should we come up with a better system here? Store times in a database? 
                    target_partitions = self._get_overlapping_target_partitions(
                        upstream_partition_key, up_start, up_end, context.instance
                    )
                    
                    for target_partition in target_partitions:
                        # 4. Queue the RunRequest
                        target_start, target_end = self._parse_dates_from_key(target_partition)
                        dependencies = self.get_dependencies(context, target_start, target_end)
                        if dependencies:
                            tags={"dependencies": dependencies.serialize()}
                            yield RunRequest(
                                            partition_key=target_partition,
                                            # UNIQUE RUN KEY: This prevents Dagster from launching 5 concurrent
                                            # identical runs if 5 upstream dependencies for the same target
                                            # time window arrive at the exact same moment.
                                            run_key=f"{self.asset_name}_{target_partition}_trigger_{record.storage_id}",
                                            tags=tags
                                        )
                            
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

    def get_dependencies(self, context, target_start, target_end):
        """Get the dependencies for the job using the DependencyResolver."""
        # 2. Iterate through each upstream dependency 
        dependency_inputs = processing_input.ProcessingInputCollection()
        context.log.info(f"Checking for all dependencies existing between {target_start} and {target_end}")
        for dep_key in self.deps_list:
            found_dep=False
            context.log.info(f"Checking out {dep_key}")
            # Fetch a list of all partition keys that have EVER been materialized for this dependency
            materialized_partitions = context.instance.get_materialized_partitions(AssetKey(dep_key))
            
            if not materialized_partitions:
                # TODO: This is probably where we'd let soft dependencies slide 
                context.log.info(f"Not enought information to process. Missing {dep_key} in range {str(target_start)} to {str(target_end)}")
                return 
            for up_partition in materialized_partitions:
                up_start, up_end = self._parse_dates_from_key(up_partition)
                
                if not up_start or not up_end:
                    continue
                    
                # 3. Apply the overlap logic (StartA < EndB and EndA > StartB)
                if up_start < target_end and up_end > target_start:
                    context.log.info(f"This partition matches: {up_partition}")
                    # 4. Fetch the actual materialization record for this overlapping partition
                    mat_event = context.instance.get_event_records(
                                event_records_filter=EventRecordsFilter(
                                    event_type=DagsterEventType.ASSET_MATERIALIZATION,
                                    asset_key=AssetKey(dep_key),
                                    asset_partitions=[up_partition],
                                ),
                                limit=1, # The most recent event is returned first
                            )
                    
                    if mat_event and mat_event[0].asset_materialization:
                        metadata = mat_event[0].asset_materialization.metadata
                        
                        if "file_names" in metadata:
                            found_dep = True # We can finally say we have found at least one dependency
                            # Dagster wraps metadata in a MetadataValue object, so we call .value
                            file_names = metadata["file_names"].value
                            # Handle both single strings and lists of files safely
                            if file_names:
                                context.log.info(f"The file names of the matching partition: {file_names}")
                            input_type = metadata["input_type"].value
                            if input_type=='science':
                                dependency_inputs.add(processing_input.ScienceInput(*file_names))
                            if input_type=='ancillary':
                                dependency_inputs.add(processing_input.AncillaryInput(*file_names))
                            if input_type=='spin':
                                dependency_inputs.add(processing_input.SpinInput(*file_names))
                            if input_type=='repoint':
                                dependency_inputs.add(
                                    processing_input.RepointInput(file_names[0])
                                )
                            if input_type=='spice':
                                dependency_inputs.add(processing_input.SPICEInput(*file_names))
            if not found_dep:
                # TODO: Check here if we have a soft dependency.
                context.log.info(f"Not enought information to process. Missing {dep_key} in range {str(target_start)} to {str(target_end)}")
                return

        return dependency_inputs

    def _calculate_crid(self):
        """Calculate CRID for a potential job.

        Return:
        ------
        str
            The calculated CRID for the potential job.
        """
        # TODO: Update CRID calculation logic or decide if it should be
        # its own class.
        # 1. Review and keep logic from current CRID logic
        # 2. Refactor current CRID logic into this funciton
        return ""

    def is_duplicate_job(self,
                         context,
                        session,
                        dependency,
                        instrument,
                        level,
                        descriptor,
                        job_command,
                        start_date,
                        repoint):
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

        '''
        This function will check if the latest file at the SDC has already been processed using 
        the gathered dependencies. To do this, it:

        1) Finds the latest file
        2) Finds the corresponding Processing Job
        3) Read in the .json dependency file from the corresponding Processing Job
        4) Compares the Ancillary and Science files in the dependency file to the current ones 
        5) If they all match, the function returns the materialization result of the latest file

        Yes, this is somewhat complicated right now. I think the best way going forward would be to 
        add the dependencies to a particular file either in the ScienceFiles or ProcessingJob tables. 
        '''
        context.log.info(f"Checking if we have already have a file of this instrument of {instrument}, this level of {level}, and this {descriptor}.")
        context.log.info(f"And this start date {start_date}, and this pointing number {repoint}.")
        if repoint:
            latest_file = session.query(models.ScienceFiles).filter(
                                            models.ScienceFiles.instrument == instrument,
                                            models.ScienceFiles.data_level == level,
                                            models.ScienceFiles.descriptor == descriptor,
                                            models.ScienceFiles.repointing == int(repoint)
                                        ).order_by(models.ScienceFiles.version.desc()).first()
        else:
            latest_file = session.query(models.ScienceFiles).filter(
                                            models.ScienceFiles.instrument == instrument,
                                            models.ScienceFiles.data_level == level,
                                            models.ScienceFiles.descriptor == descriptor,
                                            models.ScienceFiles.start_date == start_date
                                        ).order_by(models.ScienceFiles.version.desc()).first()
        if latest_file:
            context.log.info("Yes we did! Now we need to check the processing jobs to see if the same dependencies were used to create this. ")
            max_version_record = (
                session.query(models.ProcessingJob)
                .filter(models.ProcessingJob.instrument == latest_file.instrument,
                    models.ProcessingJob.data_level == latest_file.data_level,
                    models.ProcessingJob.descriptor == job_command,
                    models.ProcessingJob.start_date == latest_file.start_date,
                    models.ProcessingJob.status.in_(
                            [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                        ))
                .order_by(models.ProcessingJob.version.desc())
                .first()
            )
            if max_version_record:
                context.log.info("A job does indeed look like this one...lets check out its dependencies file")
                match = re.search(r'--dependency\s+(\S+\.json)', max_version_record.container_command)
                if match:
                    dependency_file = match.group(1)
                    print(dependency_file)
                    context.log.info(dependency_file)
                    SSM_CLIENT = boto3.client("ssm")
                    ssm_parameter_name = os.environ.get(
                        "SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key"
                    )
                    try:
                        ssm_response = SSM_CLIENT.get_parameter(
                            Name=ssm_parameter_name, WithDecryption=True
                        )
                        imap_data_access.config["API_KEY"] = ssm_response["Parameter"]["Value"]
                    except Exception as e:
                        print(f"Could not retrieve API key from SSM: {e}")
                    dependency_filepath = download(dependency_file)
                    with open(dependency_filepath) as f:
                        old_inputs = json.loads(f.read())
                    context.log.info(f"Check it out! These are the dependencies it previously ran with: {json.dumps(old_inputs)}")
                    dependencies_match = True
                    old_science_inputs = []
                    old_ancillary_inputs = []
                    new_science_inputs = []
                    new_ancillary_inputs = []
                    for dep in old_inputs:
                        if dep['type'] == "science":
                            old_science_inputs.extend(dep['files'])
                        if dep['type'] == "ancillary":
                            old_ancillary_inputs.extend(dep['files'])
                    new_inputs = json.loads(dependency.serialize())
                    context.log.info(f"Check it out! These are the dependencies that we want to run it with now: {json.dumps(new_inputs)}")
                    for dep in new_inputs:
                        if dep['type'] == "science":
                            new_science_inputs.extend(dep['files'])
                        if dep['type'] == "ancillary":
                            new_ancillary_inputs.extend(dep['files'])

                    if set(old_science_inputs) != set(new_science_inputs):
                        dependencies_match = False
                    if set(old_ancillary_inputs) != set(new_ancillary_inputs):
                        dependencies_match = False

                    if dependencies_match:
                        context.log.info(f"It's a match!")
                        # The latest file does not need to be updated. 
                        # We need to tell dagster that this asset is complete. 
                        context.log.info(f"Latest file: {latest_file.file_path}")
                        context.log.info(f"Version: {latest_file.version}")
                        return latest_file
                    else:
                        context.log.info(f"It's not a match!")
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

        Parameters
        ----------
        serialized_dependencies : str
            The serialized dependencies string.

        Returns
        -------
        str
            The first 8 characters of the SHA-256 hash of the serialized dependencies.
        """
        return hashlib.sha256(serialized_dependencies.encode("utf-8")).hexdigest()[:8]

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
